<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
      <h2 class="text-sm font-semibold text-gray-200">工作区</h2>
      <div class="text-[10px] text-gray-600 mt-0.5">工作区根: {{ wsInfo.workspace_dir || '…' }} · 写权限限工作区内</div>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4">
      <!-- 路径导航 -->
      <div class="flex items-center gap-2 mb-3 text-xs">
        <button class="text-gray-400 hover:text-gray-200" @click="browse('')">/ 根</button>
        <span v-if="currentRel" class="text-gray-500">{{ currentRel }}</span>
        <input v-model="manualPath" placeholder="工作区内路径(可选)" class="flex-1 bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
        <button class="bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-3 py-1" @click="browseByPath">浏览</button>
      </div>

      <!-- 目录内容 -->
      <div class="grid grid-cols-2 gap-1">
        <div
          v-for="e in entries"
          :key="e.name"
          class="cursor-pointer text-xs px-2 py-1.5 rounded hover:bg-[#1e1e24]"
          :class="e.type === 'dir' ? 'text-amber-400/90' : 'text-gray-400'"
          @click="e.type === 'dir' && openDir(e.name)"
        >
          <span class="mr-1">{{ e.type === 'dir' ? '📁' : '📄' }}</span>{{ e.name }}
          <span v-if="e.type === 'file'" class="text-[9px] text-gray-600 ml-1">{{ fmtSize(e.size) }}</span>
        </div>
      </div>
      <div v-if="!entries.length && !loading" class="text-gray-600 text-sm mt-4">空目录</div>

      <!-- 导入 -->
      <div class="mt-6 border-t border-[#2a2a30] pt-3">
        <div class="text-xs text-gray-300 mb-2">导入工作区内文件夹为子项目</div>
        <div class="flex gap-2 text-xs">
          <input v-model="importPath" placeholder="工作区内文件夹路径" class="flex-1 bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
          <button class="bg-emerald-700/70 hover:bg-emerald-600 text-white rounded px-3 py-1" @click="doImport">导入</button>
        </div>
        <div v-if="importResult" class="mt-1 text-[11px] text-emerald-500">{{ importResult }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../../api'

const wsInfo = ref<any>({})
const entries = ref<any[]>([])
const currentRel = ref('')
const manualPath = ref('')
const importPath = ref('')
const importResult = ref('')
const loading = ref(false)

const fmtSize = (s: number) => s == null ? '' : s >= 1024 * 1024 ? `${(s / 1024 / 1024).toFixed(1)}MB` : `${Math.round(s / 1024)}KB`

async function loadInfo() {
  try {
    const res = await api.workspace.info()
    wsInfo.value = res.data?.data || {}
  } catch {}
}

async function browse(path: string) {
  loading.value = true
  try {
    const res = await api.workspace.browse(path)
    entries.value = res.data?.data?.entries || []
    currentRel.value = res.data?.data?.relative || ''
  } catch (e: any) {
    entries.value = []
    alert(e?.response?.data?.detail ?? '浏览失败')
  } finally {
    loading.value = false
  }
}

function browseByPath() {
  browse(manualPath.value.trim())
}

function openDir(name: string) {
  const next = currentRel.value ? `${currentRel.value}/${name}` : name
  manualPath.value = next
  browse(next)
}

async function doImport() {
  if (!importPath.value.trim()) { alert('请输入工作区内文件夹路径'); return }
  try {
    const res = await api.workspace.import(importPath.value.trim())
    importResult.value = res.data?.message || res.data?.data?.project_id || '已创建'
  } catch (e: any) { alert(e?.response?.data?.detail ?? '导入失败') }
}

onMounted(() => { loadInfo(); browse('') })
</script>