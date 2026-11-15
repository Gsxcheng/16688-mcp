// Talks to the purchase-decision service (dujiao-shim) mounted on the same domain under /ops/api.
// It accepts the admin's own login token, so no second password is needed.
import { notifyError } from '@/utils/notify'

export interface Quote { ok: boolean; total: number; stock: number; message: string; at: string }
export interface PoolSource {
  type: '16688' | 'manual'
  goods_no: string
  label: string
  stock: number
  note: string
  enabled: boolean
  max_unit_cost: number
  quote: Quote | null
}
export interface PoolProduct {
  id: number
  sku_id: number
  title: string
  description: string
  cost: number
  mode: 'direct' | 'redeem'
  rule: 'cheapest' | 'order'
  max_unit_cost: number
  valid_days: number
  warn_minutes: number
  instruction: string
  hide_upstream_note: boolean
  health: 'ok' | 'near_cap' | 'down' | 'unknown'
  sources: PoolSource[]
}
export interface TraceEntry { src: string; verdict: string; total?: number; stock?: number }
export interface ProcOrder {
  order_no: string
  status: string
  product: string
  quantity: number
  supplier: string
  supplier_ref: string
  amount: string
  error: string
  created_at: string
  from: string
  code: string
  trace: TraceEntry[]
}
export interface Overview {
  live: boolean
  wallet: number | null
  min_balance: number
  orders_today: number
  max_orders: number
  unredeemed: number
  open_proposals: number
  exceptions: number
  redeem_url: string
}
export interface Goods {
  goods_no: string
  name: string
  unit_price: number
  available_quantity: number
  sales_count: number
  in_pool: string
}
export interface OwnCode {
  code: string
  product: string
  channel: string
  state: string
  issued_at: string
  expires_at: string
}
export interface ProposalSource { type?: string; goods_no?: string; name?: string; label?: string; total?: number | null; stock?: number; note?: string }
export interface Proposal {
  id: number
  status: 'open' | 'approved' | 'rejected'
  created_at: string
  type: 'new_product' | 'remove_source'
  title: string
  mode?: string
  price?: number
  cost?: number | null
  max_unit_cost?: number
  reason?: string
  risk?: string
  sources?: ProposalSource[]
}

async function call<T>(path: string, body?: unknown, quiet = false): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${localStorage.getItem('admin_token') || ''}` }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(`/ops/api/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
    })
  } catch (e) {
    if (!quiet) notifyError('采购服务连不上，请稍后再试')
    throw e
  }
  let data: any = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok || !data?.ok) {
    const msg = res.status === 401 ? '登录已过期或没有采购权限，请重新登录后台' : data?.error_message || `请求失败（${res.status}）`
    if (!quiet) notifyError(msg)
    throw new Error(msg)
  }
  return data as T
}

export const procurementAPI = {
  overview: () => call<Overview & { ok: true }>('overview'),
  pools: () => call<{ products: PoolProduct[]; hint: string }>('pools'),
  savePool: (p: Partial<PoolProduct>) => call<{ product: PoolProduct }>('pools/save', p),
  sweep: () => call<{ products: PoolProduct[] }>('sweep', {}),
  channel: (refresh = false) =>
    call<{ configured: boolean; at: string; goods: Goods[] }>(`channel/16688${refresh ? '?refresh=1' : ''}`),
  exceptions: () => call<{ orders: ProcOrder[] }>('exceptions'),
  orderAction: (action: 'deliver' | 'cancel' | 'retry', order_no: string, extra: Record<string, string> = {}) =>
    call<{ order: ProcOrder }>(`orders/${action}`, { order_no, ...extra }),
  log: () => call<{ orders: ProcOrder[] }>('log'),
  codes: () => call<{ codes: OwnCode[] }>('codes'),
  generateCodes: (sku_id: number, n: number) => call<{ batch: string; codes: string[] }>('codes/generate', { sku_id, n }),
  voidCode: (code: string) => call<object>('codes/void', { code }),
  proposals: () => call<{ proposals: Proposal[] }>('proposals'),
  decide: (id: number, action: 'approve' | 'reject') => call<object>(`proposals/${id}/${action}`, {}),
}

export const yuan = (v: number | null | undefined) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : `¥${Number(v).toFixed(2)}`)

export const VERDICTS: Record<string, string> = {
  chosen: '选中',
  candidate: '备选',
  insufficient_stock: '缺货',
  price_exceeded: '超过上限价',
  insufficient_balance: '钱包不够',
  sku_unavailable: '已下架或拒单',
  supplier_unreachable: '连不上',
  supplier_unconfigured: '未配置',
  rate_limited: '今日已到上限',
  ambiguous: '结果不确定',
  manual_fallback: '转人工',
}

export const ORDER_STATUS: Record<string, { label: string; cls: string }> = {
  delivered: { label: '已交付', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  processing: { label: '处理中', cls: 'border-blue-200 bg-blue-50 text-blue-700' },
  waiting_stock: { label: '等货源', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  awaiting_manual: { label: '待人工', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  needs_review: { label: '需核对', cls: 'border-red-200 bg-red-50 text-red-700' },
  rejected: { label: '已拒绝', cls: 'border-border bg-muted text-muted-foreground' },
  canceled: { label: '已取消', cls: 'border-border bg-muted text-muted-foreground' },
  pending: { label: '待处理', cls: 'border-border bg-muted text-muted-foreground' },
}

export const statusOf = (s: string) => ORDER_STATUS[s] || { label: s, cls: 'border-border bg-muted text-muted-foreground' }
