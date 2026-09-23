/**
 * 传输不变形（2.3 vitest 侧）：后端 Python float 经 JSON 到 JS number 全等。
 *
 * fixture 由 MyQuant/tests/consistency/test_transport_invariance.py 落盘：
 * - py_native: 引擎原生 float 的 Python 最短 repr（字符串）
 * - json_roundtrip: 经 FastAPI 响应 JSON 往返后的数值
 *
 * 断言：JSON.parse 后的数值 === Number(py_repr)——两端 IEEE754 位级互认。
 * 另锁定 NaN/Infinity 为非法 JSON（JSON.parse 抛错）——指标层泄漏 NaN 即
 * 前端整响应解析崩溃（对应 findings D-2b 风险面）。
 */
/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  '__fixtures__/metrics-transport.json',
)

interface Fixture {
  py_native: Record<'total_return' | 'sharpe_ratio' | 'max_drawdown' | 'win_rate', string> & { total_trades: number }
  json_roundtrip: Record<'total_return' | 'sharpe_ratio' | 'max_drawdown' | 'win_rate', number> & { total_trades: number }
}

describe('传输不变形：Python float → JSON → JS number', () => {
  const fixture: Fixture = JSON.parse(readFileSync(fixturePath, 'utf-8'))

  it('fixture 存在且结构完整', () => {
    expect(fixture.py_native).toBeTruthy()
    expect(fixture.json_roundtrip).toBeTruthy()
  })

  for (const key of ['total_return', 'sharpe_ratio', 'max_drawdown', 'win_rate'] as const) {
    it(`${key}: JSON 往返值 === Python repr 解析值（位级互认）`, () => {
      const fromPython = Number(fixture.py_native[key])
      expect(Number.isFinite(fromPython)).toBe(true)
      expect(Object.is(fixture.json_roundtrip[key], fromPython)).toBe(true)
    })
  }

  it('计数类仍为安全整数', () => {
    expect(Number.isSafeInteger(fixture.json_roundtrip.total_trades)).toBe(true)
    expect(fixture.json_roundtrip.total_trades).toBe(fixture.py_native.total_trades)
  })

  it('NaN/Infinity 为非法 JSON：泄漏即前端解析崩溃（D-2b 风险面锁定）', () => {
    expect(() => JSON.parse('{"total_return": NaN}')).toThrow(SyntaxError)
    expect(() => JSON.parse('{"total_return": Infinity}')).toThrow(SyntaxError)
  })
})
