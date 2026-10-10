/** 管理端与用户端共享登录；真正权限由后端逐请求检查。 */
import { createRouter, createWebHashHistory } from 'vue-router'
import LoginView from './views/LoginView.vue'
import ChatView from './views/ChatView.vue'
import AdminView from './views/AdminView.vue'
import PreferencesView from './views/PreferencesView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginView },
    { path: '/', component: ChatView },
    { path: '/admin', component: AdminView },
    { path: '/preferences', component: PreferencesView },
  ],
})

router.beforeEach((to) => {
  if (to.path !== '/login' && !sessionStorage.getItem('trip-token')) return '/login'
})

export default router
