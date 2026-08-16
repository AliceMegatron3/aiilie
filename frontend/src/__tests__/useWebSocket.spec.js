import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useWebSocket } from '../composables/useWebSocket'

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.CONNECTING
    this.sent = []
    FakeWebSocket.instances.push(this)
  }

  send(payload) {
    if (this.readyState !== FakeWebSocket.OPEN) {
      throw new Error('socket is not open')
    }
    this.sent.push(JSON.parse(payload))
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  drop() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

FakeWebSocket.instances = []

const mountSocket = () => {
  let socketApi
  const wrapper = mount(defineComponent({
    setup() {
      socketApi = useWebSocket()
      return () => null
    },
  }))
  return { socketApi, wrapper }
}

describe('useWebSocket task subscriptions', () => {
  afterEach(() => {
    vi.useRealTimers()
    FakeWebSocket.instances.length = 0
    vi.unstubAllGlobals()
  })

  it('restores active task subscriptions after reconnecting', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)

    const { socketApi, wrapper } = mountSocket()
    socketApi.subscribeTask('task-1', () => {})

    const firstConnection = FakeWebSocket.instances[0]
    firstConnection.open()
    expect(firstConnection.sent).toEqual([
      { action: 'subscribe_task', task_id: 'task-1' },
    ])

    firstConnection.drop()
    vi.advanceTimersByTime(1000)

    const secondConnection = FakeWebSocket.instances[1]
    secondConnection.open()
    expect(secondConnection.sent).toEqual([
      { action: 'subscribe_task', task_id: 'task-1' },
    ])

    wrapper.unmount()
  })

  it('does not restore a task after it has been unsubscribed', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)

    const { socketApi, wrapper } = mountSocket()
    socketApi.subscribeTask('task-1', () => {})
    const firstConnection = FakeWebSocket.instances[0]
    firstConnection.open()
    socketApi.unsubscribeTask('task-1')

    firstConnection.drop()
    vi.advanceTimersByTime(1000)

    const secondConnection = FakeWebSocket.instances[1]
    secondConnection.open()
    expect(secondConnection.sent).toEqual([])

    wrapper.unmount()
  })
})