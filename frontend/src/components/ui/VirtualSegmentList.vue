<template>
  <div class="h-full w-full">
    <DynamicScroller
      :items="segments"
      :min-item-size="60"
      class="h-full scroller"
      key-field="id"
    >
      <template #default="{ item, index, active }">
        <DynamicScrollerItem
          :item="item"
          :active="active"
          :size-dependencies="[item.content, item.status]"
          :data-index="index"
        >
          <div :data-index="index" class="p-3 mb-2 bg-[#27272a]/50 border border-gray-800 rounded-lg text-gray-300">
            <div class="flex justify-between items-center mb-1">
              <span class="text-xs font-mono text-purple-400">#{{ item.sequence_order || index + 1 }} - {{ item.status || 'PENDING' }}</span>
              <span class="text-[10px] text-gray-500">ID: {{ item.id || item.segment_id }}</span>
            </div>
            <div class="whitespace-pre-wrap leading-relaxed text-sm">
              {{ item.content || item.content_payload }}
            </div>
            <div v-if="item.error_message" class="mt-2 text-xs text-red-400">
              错误: {{ item.error_message }}
            </div>
          </div>
        </DynamicScrollerItem>
      </template>
    </DynamicScroller>
  </div>
</template>

<script setup lang="ts">
import { defineProps } from 'vue'

defineProps({
  segments: {
    type: Array,
    required: true,
  }
})
</script>

<style scoped>
.scroller {
  height: 100%;
}
</style>
