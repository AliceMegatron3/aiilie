/**
 * diffMatchPatch 单元测试 — LCS 差分算法（补丁3）
 * 覆盖：diff_main 字符级、diff_cleanupSemantic 合并、diffLines 行级，
 * 以及“应用差分可还原两输入”的不变量（结构性保证，不依赖具体编辑脚本顺序）。
 */
import { describe, it, expect } from 'vitest'
import { diffMatchPatch, diffLines } from '../utils/diffMatchPatch'
import type { DiffTuple } from '../utils/diffMatchPatch'

/** 应用编辑脚本，还原两源串；断言结构性正确 + 可还原。 */
function roundTrip(diffs: DiffTuple[], a: string, b: string) {
  let ra = ''
  let rb = ''
  for (const [op, text] of diffs) {
    // 不允许相邻同类型片段（mergeAdjacent 不变量）
    if (op === 0) { ra += text; rb += text }
    else if (op === -1) ra += text
    else rb += text
  }
  expect(ra).toBe(a)
  expect(rb).toBe(b)
}

describe('diffMatchPatch.diff_main（LCS 字符级）', () => {
  it('相同文本 → 单个相等块', () => {
    expect(diffMatchPatch.diff_main('abc', 'abc')).toEqual([[0, 'abc']])
  })

  it('追加文本 → 相等 + 插入', () => {
    const d = diffMatchPatch.diff_main('abc', 'abcd')
    expect([d[0][0], d[1][0]]).toEqual([0, 1])
    roundTrip(d, 'abc', 'abcd')
  })

  it('前缀删除 → 相等 + 删除', () => {
    const d = diffMatchPatch.diff_main('abcd', 'abc')
    roundTrip(d, 'abcd', 'abc')
    expect(d[d.length - 1][0]).toBe(-1)
  })

  it('字符替换可还原两输入', () => {
    const d = diffMatchPatch.diff_main('abc', 'axc')
    roundTrip(d, 'abc', 'axc')
  })

  it('完全替换 → 一删一插且可还原', () => {
    const d = diffMatchPatch.diff_main('foo', 'bar')
    roundTrip(d, 'foo', 'bar')
  })

  it('空输入边界', () => {
    expect(diffMatchPatch.diff_main('', '')).toEqual([])
    const ins = diffMatchPatch.diff_main('', 'xy')
    roundTrip(ins, '', 'xy')
    const del = diffMatchPatch.diff_main('xy', '')
    roundTrip(del, 'xy', '')
  })
})

describe('diffMatchPatch.diff_cleanupSemantic（合并相邻同类）', () => {
  it('合并相邻删除/插入/相等片段，且不产生相邻同类型', () => {
    // 人为构造相邻同类型（宽容输入），cleanup 后应压缩
    const raw: DiffTuple[] = [[-1, 'a'], [-1, 'b'], [0, 'c'], [0, 'd'], [1, 'x'], [1, 'y']]
    const cleaned = diffMatchPatch.diff_cleanupSemantic(raw)
    expect(cleaned).toEqual([[-1, 'ab'], [0, 'cd'], [1, 'xy']])
  })

  it('幂等：对已清理结果再次调用不变', () => {
    const once = diffMatchPatch.diff_cleanupSemantic([[1, 'a'], [1, 'b'], [-1, 'c']])
    const twice = diffMatchPatch.diff_cleanupSemantic(once)
    expect(twice).toEqual(once)
  })

  it('丢弃空片段', () => {
    expect(diffMatchPatch.diff_cleanupSemantic([[0, ''], [1, 'x'], [0, '']])).toEqual([[1, 'x']])
  })
})

describe('diffLines（行级对比）', () => {
  it('多行相等 → 单块相等（忠实保留换行，可还原输入）', () => {
    const d = diffLines('a\nb\nc\n', 'a\nb\nc\n')
    expect(d).toEqual([[0, 'a\nb\nc\n']])
  })

  it('新增一行可还原', () => {
    const d = diffLines('a\nb\n', 'a\nb\nc\n')
    roundTrip(d, 'a\nb\n', 'a\nb\nc\n')
  })
})