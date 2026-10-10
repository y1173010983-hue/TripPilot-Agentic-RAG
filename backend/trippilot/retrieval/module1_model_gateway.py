"""模型网关：集中封装标准兼容接口、结构化输出与真实流式文本，不打印密钥。"""
import json
import time
import httpx
from ..config import get_settings


class ModelUnavailable(RuntimeError):
    pass


class ModelGateway:
    def __init__(self):
        self.settings = get_settings()

    def require_configuration(self):
        if not all((self.settings.llm_api_key, self.settings.llm_model, self.settings.llm_base_url)):
            raise ModelUnavailable("大模型未配置，请联系管理员填写模型地址、名称和 API Key")

    def complete_json(self, messages: list[dict], max_tokens=1800) -> dict:
        """请求 JSON 并解析；仅限 429/5xx 重试，服务端响应正文不写日志。"""
        self.require_configuration()
        payload = {"model": self.settings.llm_model, "messages": messages,
                   "temperature": 0.2, "max_tokens": max_tokens,
                   "response_format": {"type": "json_object"}}
        with httpx.Client(timeout=90) as client:
            for attempt in range(3):
                try:
                    response = client.post(self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                                           headers={"Authorization": "Bearer " + self.settings.llm_api_key}, json=payload)
                except httpx.TimeoutException as exc:
                    # 结构化请求尚未产生用户可见内容，可安全重试一次。
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    raise ModelUnavailable("模型服务响应超时，请稍后重试当前问题") from exc
                except httpx.RequestError as exc:
                    raise ModelUnavailable("无法连接模型服务，请稍后重试当前问题") from exc
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                if response.status_code != 200:
                    raise ModelUnavailable(f"模型服务返回 HTTP {response.status_code}，请检查模型权限与配置")
                content = response.json()["choices"][0]["message"]["content"]
                try:
                    return json.loads(content)
                except (ValueError, TypeError):
                    raise ModelUnavailable("模型没有返回有效结构化结果，请重试") from None
        raise ModelUnavailable("模型暂时不可用")

    def stream_answer(self, messages: list[dict], on_delta) -> str:
        """把供应商真实增量交给调用方；开始输出后不自动重试以免重复文本。"""
        self.require_configuration()
        output = []
        with httpx.Client(timeout=90) as client:
            with client.stream("POST", self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                               headers={"Authorization": "Bearer " + self.settings.llm_api_key},
                               json={"model": self.settings.llm_model, "messages": messages,
                                     "temperature": 0.3, "max_tokens": 3000, "stream": True}) as response:
                if response.status_code != 200:
                    raise ModelUnavailable(f"模型流式服务返回 HTTP {response.status_code}")
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    if line[6:] == "[DONE]":
                        break
                    event = json.loads(line[6:])
                    choices = event.get("choices", [])
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if delta:
                        output.append(delta)
                        on_delta(delta)
        if not output:
            raise ModelUnavailable("模型返回空回复")
        return "".join(output)
