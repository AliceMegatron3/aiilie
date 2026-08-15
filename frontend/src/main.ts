import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import VueVirtualScroller from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import './index.css'
import App from './App.vue'
import { toast } from './utils/toast'
import { installBehaviorTracker } from './utils/behaviorTracker'

const app = createApp(App)

app.config.errorHandler = (err: unknown, instance, info: string) => {
    console.error('Vue Error:', err, info)
    const errorMessage = err instanceof Error ? err.message : String(err)
    toast.error('系统异常: ' + errorMessage)
}

app.use(createPinia())
app.use(router)
app.use(VueVirtualScroller)
// 批次7：安装用户行为采集器（操作路径/页面停留/报错路径 → 后端轮转存储）
installBehaviorTracker(router)
app.mount('#app')
