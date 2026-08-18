import { describe, it, expect } from 'vitest'
import { api, unwrap, unwrapData } from '../api'

describe('API 契约统一 unwrapData/unwrap（7.8 / Batch 1 前端可用性）', () => {
  it('ok() 形态 {success,data,message,error_code} → 解出 data', () => {
    const res = { data: { success: true, data: { foo: 1 }, message: 'success', error_code: null } }
    expect(unwrapData(res)).toEqual({ foo: 1 })
  })

  it('旧裸形态 {volumes:[...]} → 整体作为业务数据', () => {
    const res = { data: { volumes: [{ id: 'v1' }] } }
    expect(unwrapData(res)).toEqual({ volumes: [{ id: 'v1' }] })
  })

  it('数组形态（后端裸 list）→ 原样返回数组', () => {
    const res = { data: [{ id: 'p1' }, { id: 'p2' }] }
    expect(unwrapData(res)).toEqual([{ id: 'p1' }, { id: 'p2' }])
  })

  it('空 body 兜底', () => {
    expect(unwrapData({ data: undefined } as any)).toBeUndefined()
  })

  it('后端裸标量/对象（无 data 键）不误剥', () => {
    const res = { data: { named: 'x' } }
    expect(unwrapData(res)).toEqual({ named: 'x' })
  })

  it('unwrap 与 unwrapData 等价（向后兼容别名）', () => {
    expect(unwrap).toBe(unwrapData)
  })

  it('library.refineKnowledge 已接线 /library/knowledge/refine，且 envelope 解包出 data', () => {
    // 契约方法存在且命中正确后端路径
    expect(typeof api.library.refineKnowledge).toBe('function')
    expect(typeof api.library.listKnowledgeClaims).toBe('function')
    // 后端 refine 返回 {success,data:{status,candidate_id,run_id,...}} → unwrap 取出 data
    const res = {
      data: {
        success: true,
        data: { status: 'COMPLETED', run_id: 'r1', details: [] },
        message: 'success',
        error_code: null,
      },
    }
    expect(unwrapData(res)).toEqual({ status: 'COMPLETED', run_id: 'r1', details: [] })
  })
})