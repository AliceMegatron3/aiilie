<template>
  <div class="w-full bg-[#1c1c20] border border-gray-700 rounded-lg flex flex-col overflow-hidden">
    <!-- 播放器头部 -->
    <div class="flex items-center justify-between px-4 py-2.5 border-b border-[#2a2a30] shrink-0">
      <div class="flex items-center gap-2">
        <button
          class="w-9 h-9 rounded-full flex items-center justify-center transition-colors"
          :class="isPlaying ? 'bg-indigo-600 hover:bg-indigo-500 text-white' : 'bg-[#2a2a30] hover:bg-gray-600 text-gray-200'"
          @click="togglePlay"
          :disabled="!hasAudio"
        >
          <span v-if="!isPlaying">▶</span><span v-else>⏸</span>
        </button>
        <div>
          <div class="text-xs font-semibold text-gray-200">{{ title }}</div>
          <div class="text-[10px] text-gray-500 font-mono">{{ currentTimeText }} / {{ durationText }}</div>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-model="currentVoice"
          class="bg-[#2a2a30] border border-gray-700 rounded px-2 py-1 text-[10px] text-gray-300 focus:outline-none"
        >
          <option value="">默认音色</option>
          <option v-for="(voice, char) in voiceProfiles" :key="char" :value="voice">{{ char }} · {{ voice }}</option>
        </select>
        <button
          class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2 py-1 rounded border border-gray-700 transition-colors"
          @click="synthesizeCurrent"
        >合成当前段</button>
      </div>
    </div>

    <!-- 文本同步高亮区 -->
    <div class="flex-1 overflow-y-auto px-4 py-3 max-h-48" ref="textAreaRef">
      <div v-if="segments.length === 0" class="text-xs text-gray-500">点击「分割文本」加载旁白/对话分段</div>
      <div
        v-for="(seg, i) in segments"
        :key="i"
        class="mb-2.5 p-2 rounded transition-all duration-150 cursor-pointer"
        :class="segmentClass(i)"
        @click="playSegment(i)"
      >
        <div v-if="seg.kind === 'dialogue'" class="flex items-center gap-1.5 mb-0.5">
          <span class="px-1.5 py-0.5 rounded text-[9px] bg-purple-900/50 text-purple-300">对白</span>
          <span class="text-[10px] font-semibold" :class="speakerColor(seg.speaker)">{{ seg.speaker || '未知角色' }}</span>
          <button
            v-if="seg.speaker"
            class="text-[9px] text-red-400/80 hover:text-red-300 border border-red-900/50 rounded px-1.5 hover:bg-red-950/50 transition-colors"
            @click.stop="markOOC(seg)"
            title="标记该角色此段对话为 OOC（角色失格）"
          >OOC</button>
        </div>
        <span v-else class="px-1.5 py-0.5 rounded text-[9px] bg-slate-800 text-slate-400">旁白</span>
        <p class="text-xs text-gray-300 leading-relaxed mt-1" :class="{ 'text-indigo-300': activeIndex === i }">{{ seg.text }}</p>
      </div>
    </div>

    <!-- 操作区 -->
    <div class="px-4 py-2.5 border-t border-[#2a2a30] flex items-center gap-2 shrink-0">
      <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded border border-gray-700 transition-colors" @click="splitText">分割文本</button>
      <span v-if="synthState" class="text-[10px] font-mono" :class="synthState === 'ready' ? 'text-emerald-400' : 'text-amber-400'">{{ synthState }}</span>
    </div>

    <audio ref="audioRef" :src="audioSrc" @timeupdate="onTimeUpdate" @ended="onEnded" @play="isPlaying = true" @pause="isPlaying = false" class="hidden"></audio>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { toast } from '../utils/toast'
import { api } from '../api'

const props = defineProps({
  text: { type: String, default: '' },
  projectId: { type: String, default: null },
  docId: { type: String, default: null },
  voiceProfiles: { type: Object, default: () => ({}) },
  title: { type: String, default: '朗读' },
})

const audioRef = ref(null)
const textAreaRef = ref(null)
const segments = ref([])
const activeIndex = ref(-1)
const isPlaying = ref(false)
const hasAudio = ref(false)
const audioSrc = ref('')
const synthState = ref('')
const currentVoice = ref('')
const currentTime = ref(0)
const duration = ref(0)

const currentTimeText = computed(() => {
  const s = Math.floor(currentTime.value)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})
const durationText = computed(() => {
  const s = Math.floor(duration.value)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})

const speakerColor = (speaker) => {
  if (!speaker) return 'text-gray-400'
  const palette = ['text-sky-400', 'text-emerald-400', 'text-pink-400', 'text-amber-400', 'text-violet-400']
  let hash = 0
  for (const ch of speaker) hash = (hash * 31 + ch.charCodeAt(0)) | 0
  return palette[Math.abs(hash) % palette.length]
}

const segmentClass = (i) => activeIndex.value === i
  ? 'bg-indigo-950/50 border border-indigo-700/60'
  : 'bg-[#1f1f24] border border-transparent hover:border-gray-700'

async function splitText() {
  if (!props.text) return toast.warning('无文本内容')
  const res = await api.tts.segment(props.text, Object.keys(props.voiceProfiles))
  segments.value = res.data?.data || []
  activeIndex.value = -1
  toast.success(`分割完成：${segments.value.length} 段`)
}

async function synthesizeSegment(seg) {
  synthState.value = '合成中...'
  try {
    const res = await api.tts.synthesize(seg.text, currentVoice.value || null)
    const file = res.data?.data?.file_name
    if (!file) throw new Error('未返回音频文件')
    audioSrc.value = api.tts.audioURL(file)
    hasAudio.value = true
    synthState.value = 'ready'
  } catch (e) {
    synthState.value = 'failed'
    toast.error('合成失败：' + (e.response?.data?.detail || e.message))
  }
}

async function playSegment(i) {
  const seg = segments.value[i]
  if (!seg) return
  activeIndex.value = i
  await synthesizeSegment(seg)
  // 播放并同步高亮
  const audio = audioRef.value
  if (audio) {
    audio.currentTime = 0
    await audio.play()
  }
  scrollActiveIntoView()
}

async function synthesizeCurrent() {
  if (activeIndex.value < 0) return toast.warning('先选择一段文本')
  await synthesizeSegment(segments.value[activeIndex.value])
}

function togglePlay() {
  const audio = audioRef.value
  if (!audio || !hasAudio.value) return
  if (audio.paused) audio.play()
  else audio.pause()
}

function onTimeUpdate() {
  const audio = audioRef.value
  if (!audio) return
  currentTime.value = audio.currentTime
  duration.value = audio.duration || 0
  // 播放结束前 500ms 自动高亮下一段
  if (duration.value - audio.currentTime < 0.5 && activeIndex.value < segments.value.length - 1) {
    playSegment(activeIndex.value + 1)
  }
}

function onEnded() {
  isPlaying.value = false
  if (activeIndex.value < segments.value.length - 1) {
    playSegment(activeIndex.value + 1)
  }
}

function scrollActiveIntoView() {
  if (textAreaRef.value) {
    const el = textAreaRef.value.querySelectorAll('div[class*="mb-2.5"]')[activeIndex.value]
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}

async function markOOC(seg) {
  if (!props.projectId || !props.docId) return toast.warning('缺少项目/文档上下文，无法提交 OOC 反馈')
  const ok = await toast.confirm(`确认将「${seg.speaker}」此段对话标记为 OOC（角色失格）并写入文档学习系统？`)
  if (!ok) return
  await api.tts.oocFeedback(props.projectId, props.docId, seg.speaker, seg.text)
  toast.success('OOC 反馈已写入批次3文档学习系统')
}

watch(() => props.text, () => {
  segments.value = []
  activeIndex.value = -1
})
</script>
