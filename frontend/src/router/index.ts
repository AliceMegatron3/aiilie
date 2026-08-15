import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/projects'
  },
  {
    path: '/projects',
    component: () => import('../components/views/ProjectsView.vue')
  },
  {
    path: '/projects/:projectId/docs/:docId',
    component: () => import('../components/views/DocumentEditor.vue'),
    props: true
  },
  {
    path: '/library',
    component: () => import('../components/views/LibraryView.vue')
  },
  {
    path: '/agents',
    component: () => import('../components/views/AgentsView.vue')
  },
  {
    path: '/templates',
    component: () => import('../components/views/TemplatesView.vue')
  },
  {
    path: '/emotion',
    component: () => import('../components/views/EmotionView.vue')
  },
  {
    path: '/timeline',
    component: () => import('../components/views/TimelineView.vue')
  },
  {
    path: '/deep-think',
    component: () => import('../components/views/DeepThinkView.vue')
  },
  {
    path: '/sessions',
    component: () => import('../components/views/SessionsView.vue')
  },
  {
    path: '/reflections',
    component: () => import('../components/views/ReflectionsView.vue')
  },
  {
    path: '/settings',
    component: () => import('../components/views/SettingsView.vue')
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
