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
  },
  {
    path: '/narrative',
    component: () => import('../components/views/NarrativeView.vue')
  },
  {
    path: '/lockfield',
    component: () => import('../components/views/LockfieldView.vue')
  },
  {
    path: '/knowledge-gaps',
    component: () => import('../components/views/KnowledgeGapsView.vue')
  },
  {
    path: '/ensemble-history',
    component: () => import('../components/views/EnsembleHistoryView.vue')
  },
  {
    path: '/plugins',
    component: () => import('../components/views/PluginsView.vue')
  },
  {
    path: '/workspace',
    component: () => import('../components/views/WorkspaceView.vue')
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
