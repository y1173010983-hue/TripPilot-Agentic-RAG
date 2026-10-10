"""跨接口验收：上传、后台处理、权限过滤、停用删除和版本发布。"""
from trippilot.worker import run_one


def upload_ready(client, headers, visibility="public", content="北京星河博物馆提供亲子历史展览，建议预留两小时。"):
    r = client.post("/api/v1/admin/knowledge-bases", headers=headers,
                    json={"name": "北京旅游", "visibility": visibility})
    assert r.status_code == 201, r.text
    kb = r.json()["id"]
    r = client.post(f"/api/v1/admin/knowledge-bases/{kb}/documents", headers=headers,
                    files={"file": ("guide.md", content.encode(), "text/markdown")}, data={"city": "北京"})
    assert r.status_code == 202, r.text
    job = r.json()
    assert run_one(job["job_id"])
    r = client.get(f"/api/v1/admin/ingestion-jobs/{job['job_id']}", headers=headers)
    assert r.json()["status"] == "completed", r.text
    return kb, job


def test_upload_search_disable_delete(client, accounts):
    kb, job = upload_ready(client, accounts["admin"])
    response = client.post("/api/v1/knowledge/search", headers=accounts["traveler"], json={"query": "北京博物馆"})
    assert response.status_code == 200, response.text
    assert response.json()[0]["document_id"] == job["document_id"]
    download = client.get(f"/api/v1/admin/documents/{job['document_id']}/original", headers=accounts["admin"])
    assert "星河" in download.content.decode()
    assert client.get(f"/api/v1/admin/documents/{job['document_id']}/original", headers=accounts["traveler"]).status_code == 403
    client.patch(f"/api/v1/admin/documents/{job['document_id']}", headers=accounts["admin"], json={"enabled": False})
    assert client.post("/api/v1/knowledge/search", headers=accounts["traveler"], json={"query": "博物馆"}).json() == []
    client.delete(f"/api/v1/admin/documents/{job['document_id']}", headers=accounts["admin"])
    assert run_one()


def test_private_and_dedup(client, accounts):
    kb, job = upload_ready(client, accounts["admin"], "private", "私有旅游资料：月光酒店旁有独家餐厅。")
    assert client.post("/api/v1/knowledge/search", headers=accounts["traveler"], json={"query": "月光酒店"}).json() == []
    r = client.post(f"/api/v1/admin/knowledge-bases/{kb}/documents", headers=accounts["admin"],
                    files={"file": ("other.md", "私有旅游资料：月光酒店旁有独家餐厅。".encode())})
    assert r.json()["duplicate"] is True
    assert r.json()["document_id"] == job["document_id"]


def test_invalid_and_empty_text(client, accounts):
    kb = client.post("/api/v1/admin/knowledge-bases", headers=accounts["admin"], json={"name": "测试"}).json()["id"]
    url = f"/api/v1/admin/knowledge-bases/{kb}/documents"
    assert client.post(url, headers=accounts["admin"], files={"file": ("wrong.pdf", b"not pdf")}).status_code == 415
    r = client.post(url, headers=accounts["admin"], files={"file": ("empty.txt", b"    ")})
    run_one(r.json()["job_id"])
    assert client.get(f"/api/v1/admin/ingestion-jobs/{r.json()['job_id']}", headers=accounts["admin"]).json()["status"] == "failed"


def test_version_switch_removes_old_evidence(client, accounts):
    kb, job = upload_ready(client, accounts["admin"], content="星河景区旧门票是二十元。")
    response = client.post(f"/api/v1/admin/knowledge-bases/{kb}/documents", headers=accounts["admin"],
                           data={"document_id": job["document_id"]}, files={"file": ("new.md", "星河景区新门票是三十元。".encode())})
    assert response.status_code == 202
    run_one(response.json()["job_id"])
    hits = client.post("/api/v1/knowledge/search", headers=accounts["traveler"], json={"query": "星河景区"}).json()
    assert all("二十元" not in hit["content"] for hit in hits)
