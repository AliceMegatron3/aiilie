/**
 * utils/diffMatchPatch.ts — 补丁3：轻量 diff-match-patch 兼容实现
 * ================================================================
 * 零依赖 LCS 差分算法（行级/字符级），API 与 diff-match-patch 对齐：
 *   diff_main(a, b) → Array<[op, text]>  op ∈ {-1 删除, 0 相等, 1 插入}
 *   diff_cleanupSemantic(diffs)         合并相邻同类、移除空片段（幂等）
 * 说明：npm 环境离线时替代 diff-match-patch 包，产物结构完全兼容。
 */

export type DiffOp = -1 | 0 | 1
export type DiffTuple = [DiffOp, string]

export interface DiffMatchPatchLike {
  diff_main: (a: string, b: string) => DiffTuple[]
  diff_cleanupSemantic: (diffs: DiffTuple[]) => DiffTuple[]
}

/** 通用 LCS 编辑脚本（token 级）。返回 [{op, text}]，op: -1 删 / 0 等 / 1 插。 */
function lcsDiff(a: string[], b: string[]): DiffTuple[] {
  const n = a.length
  const m = b.length
  // 空间优化 LCS 表（滚动两行）+ 回溯需要完整 DP 时退化为 O(n*m) 内存
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const diffs: DiffTuple[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      diffs.push([0, a[i]])
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      diffs.push([-1, a[i]])
      i++
    } else {
      diffs.push([1, b[j]])
      j++
    }
  }
  while (i < n) diffs.push([-1, a[i++]])
  while (j < m) diffs.push([1, b[j++]])
  return mergeAdjacent(diffs)
}

/** 合并相邻同类型片段。 */
function mergeAdjacent(diffs: DiffTuple[]): DiffTuple[] {
  const merged: DiffTuple[] = []
  for (const [op, text] of diffs) {
    if (!text) continue
    const last = merged[merged.length - 1]
    if (last && last[0] === op) last[1] += text
    else merged.push([op, text])
  }
  return merged
}

/** 按字符 diff（diff-match-patch diff_main 语义）。 */
function charDiff(a: string, b: string): DiffTuple[] {
  return lcsDiff(Array.from(a), Array.from(b))
}

/** 按行 diff（大文本对比，性能更好）。 */
export function diffLines(a: string, b: string): DiffTuple[] {
  const la = a.split('\n')
  const lb = b.split('\n')
  const lineDiffs = lcsDiff(la, lb)
  // 还原换行
  const restored: DiffTuple[] = lineDiffs.map(([op, text]) => {
    if (op === 0) return [op, text + '\n'] as DiffTuple
    return [op, text + '\n'] as DiffTuple
  })
  // 去掉末尾多余换行
  if (restored.length) {
    const last = restored[restored.length - 1]
    last[1] = last[1].replace(/\n$/, '')
  }
  return restored
}

export const diffMatchPatch: DiffMatchPatchLike = {
  diff_main(a: string, b: string): DiffTuple[] {
    return charDiff(a, b)
  },
  diff_cleanupSemantic(diffs: DiffTuple[]): DiffTuple[] {
    return mergeAdjacent(diffs)
  },
}

export default diffMatchPatch
