import { useEffect, useRef } from 'react'
import { useThemeValue } from '../theme'

const LIME = '198, 255, 61'
const VIOLET = '139, 92, 246'
const FRAME_MS = 1000 / 30

interface Column {
  x: number
  head: number
  speed: number
  length: number
  chars: string[]
  lime: boolean
}

const bit = () => (Math.random() < 0.5 ? '0' : '1')

/**
 * The noxsw.kz backdrop: falling 0/1 streams (lime and violet), two soft glows and a vignette.
 * Dark theme only; static frame with prefers-reduced-motion; paused while the tab is hidden.
 * `dim` lowers the rain under dense content (the cabinet), auth pages get it at full strength.
 */
export function Backdrop({ dim = false }: { dim?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const theme = useThemeValue()
  const dark = theme === 'dark'

  useEffect(() => {
    const canvas = ref.current
    if (!dark || !canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const peak = dim ? 0.32 : 0.55
    let width = 0
    let height = 0
    let cell = 18
    let rows = 0
    let columns: Column[] = []
    let last = 0
    let raf = 0

    const makeColumn = (x: number, scatter: boolean): Column => {
      const length = 8 + Math.floor(Math.random() * 22)
      return {
        x,
        head: scatter ? Math.random() * (rows + length) : -Math.random() * rows,
        speed: 0.15 + Math.random() * 0.45,
        length,
        chars: Array.from({ length }, bit),
        lime: Math.random() < 0.8,
      }
    }

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      cell = width < 640 ? 14 : 18
      rows = Math.ceil(height / cell)
      columns = []
      // airy on desktop, sparser on phones so the form stays readable
      const density = width < 640 ? 0.35 : 0.6
      for (let x = cell / 2; x < width; x += cell) if (Math.random() < density) columns.push(makeColumn(x, true))
      ctx.font = `${cell - 4}px 'JetBrains Mono', ui-monospace, monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
    }

    const draw = () => {
      ctx.clearRect(0, 0, width, height)
      for (const col of columns) {
        const rgb = col.lime ? LIME : VIOLET
        for (let k = 0; k < col.length; k++) {
          const row = Math.floor(col.head) - k
          if (row < 0 || row > rows) continue
          const alpha = (1 - k / col.length) * peak
          ctx.fillStyle = k === 0 ? `rgba(225, 255, 170, ${Math.min(alpha + 0.3, 0.9)})` : `rgba(${rgb}, ${alpha})`
          ctx.fillText(col.chars[k], col.x, row * cell + cell / 2)
        }
      }
    }

    const step = () => {
      for (let i = 0; i < columns.length; i++) {
        const col = columns[i]
        col.head += col.speed
        if (Math.random() < 0.08) col.chars[Math.floor(Math.random() * col.length)] = bit()
        if (col.head - col.length > rows) columns[i] = makeColumn(col.x, false)
      }
    }

    const frame = (now: number) => {
      if (now - last >= FRAME_MS) {
        last = now
        step()
        draw()
      }
      raf = requestAnimationFrame(frame)
    }

    const onVisibility = () => {
      cancelAnimationFrame(raf)
      if (!document.hidden && !reduce) raf = requestAnimationFrame(frame)
    }
    const onResize = () => {
      resize()
      draw()
    }

    resize()
    draw()
    if (!reduce) raf = requestAnimationFrame(frame)
    window.addEventListener('resize', onResize)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [dark, dim])

  if (!dark) return null
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute -top-40 -left-40 size-[560px] rounded-full bg-[radial-gradient(circle,rgb(139_92_246/0.22)_0%,transparent_70%)] blur-3xl" />
      <div className="absolute top-1/3 -right-48 size-[640px] rounded-full bg-[radial-gradient(circle,rgb(198_255_61/0.12)_0%,transparent_70%)] blur-3xl" />
      <canvas ref={ref} className="absolute inset-0 size-full" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgb(5_6_8/0)_40%,rgb(5_6_8/0.75)_100%)]" />
    </div>
  )
}
