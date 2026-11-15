<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogScrollContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import TableSkeleton from '@/components/TableSkeleton.vue'
import { procurementAPI, yuan, type PoolProduct, type PoolSource } from '@/api/procurement'
import { notifyError, notifySuccess } from '@/utils/notify'

const loading = ref(true)
const sweeping = ref(false)
const saving = ref(false)
const products = ref<PoolProduct[]>([])
const showModal = ref(false)

const blank = () => ({
  sku_id: 0,
  title: '',
  mode: 'redeem' as 'direct' | 'redeem',
  rule: 'cheapest' as 'cheapest' | 'order',
  cost: 0,
  max_unit_cost: 0,
  valid_days: 7,
  warn_minutes: 0,
  instruction: '',
  hide_upstream_note: false,
  sources: [] as PoolSource[],
})
const form = reactive(blank())
const newGoods = ref('')
const newManual = reactive({ label: '', stock: 0 })

const HEALTH: Record<string, { label: string; cls: string }> = {
  ok: { label: '在售', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  near_cap: { label: '成本快到上限', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  down: { label: '缺货', cls: 'border-red-200 bg-red-50 text-red-700' },
  unknown: { label: '未巡检', cls: 'border-border bg-muted text-muted-foreground' },
}

const cheapest = (p: PoolProduct) => {
  const ok = p.sources.filter((s) => s.enabled && s.quote?.ok && s.quote.stock > 0).map((s) => s.quote!.total)
  return ok.length ? Math.min(...ok) : null
}

const load = async () => {
  loading.value = true
  try {
    products.value = (await procurementAPI.pools()).products
  } catch {
    products.value = []
  } finally {
    loading.value = false
  }
}

const sweep = async () => {
  sweeping.value = true
  try {
    products.value = (await procurementAPI.sweep()).products
    notifySuccess('巡检完成，价格和库存已更新')
  } catch {
    /* notified */
  } finally {
    sweeping.value = false
  }
}

const openEdit = (p?: PoolProduct) => {
  Object.assign(form, blank())
  if (p) {
    Object.assign(form, {
      sku_id: p.sku_id, title: p.title, mode: p.mode, rule: p.rule, cost: p.cost, max_unit_cost: p.max_unit_cost,
      valid_days: p.valid_days, warn_minutes: p.warn_minutes, instruction: p.instruction,
      hide_upstream_note: p.hide_upstream_note, sources: p.sources.map((s) => ({ ...s })),
    })
  }
  newGoods.value = ''
  newManual.label = ''
  newManual.stock = 0
  showModal.value = true
}

const addGoods = () => {
  const g = newGoods.value.trim().toUpperCase()
  if (!g) return
  if (form.sources.some((s) => s.type === '16688' && s.goods_no === g)) {
    notifyError('这个货源已经在池子里了')
    return
  }
  form.sources.push({ type: '16688', goods_no: g, label: '', stock: 0, note: '', enabled: true, max_unit_cost: 0, quote: null })
  newGoods.value = ''
}

const addManual = () => {
  if (!newManual.label.trim()) return
  form.sources.push({ type: 'manual', goods_no: '', label: newManual.label.trim(), stock: Number(newManual.stock) || 0, note: '', enabled: true, max_unit_cost: 0, quote: null })
  newManual.label = ''
  newManual.stock = 0
}

const move = (i: number, d: number) => {
  const j = i + d
  if (j < 0 || j >= form.sources.length) return
  const s = form.sources.splice(i, 1)[0]!
  form.sources.splice(j, 0, s)
}

const save = async () => {
  if (!form.title.trim()) {
    notifyError('商品名称不能为空')
    return
  }
  saving.value = true
  try {
    await procurementAPI.savePool({
      ...(form.sku_id ? { sku_id: form.sku_id } : {}),
      title: form.title.trim(), mode: form.mode, rule: form.rule, cost: Number(form.cost) || 0,
      max_unit_cost: Number(form.max_unit_cost) || 0, valid_days: Number(form.valid_days) || 7,
      warn_minutes: Number(form.warn_minutes) || 0, instruction: form.instruction,
      hide_upstream_note: form.hide_upstream_note,
      sources: form.sources.map(({ quote: _q, ...s }) => s) as PoolSource[],
    })
    notifySuccess('已保存，下一单就按新设置采购')
    showModal.value = false
    await load()
  } catch {
    /* notified */
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-semibold">货源池</h1>
        <p class="mt-1 text-sm text-muted-foreground">每个商品一个池子。下单或兑换时实时询价，选有货、不超上限价里最便宜的；每 30 分钟自动巡检一次。价格均含 16688 手续费。</p>
      </div>
      <div class="flex gap-2">
        <Button variant="outline" :disabled="sweeping" @click="sweep">{{ sweeping ? '巡检中…' : '立即巡检' }}</Button>
        <Button @click="openEdit()">新建商品</Button>
      </div>
    </div>

    <div class="rounded-xl border border-border bg-card overflow-x-auto">
      <Table class="min-w-[900px]">
        <TableHeader class="border-b border-border bg-muted/40 text-xs uppercase text-muted-foreground">
          <TableRow>
            <TableHead class="px-6 py-3">商品</TableHead>
            <TableHead class="px-6 py-3">发货方式</TableHead>
            <TableHead class="px-6 py-3">选源规则</TableHead>
            <TableHead class="px-6 py-3 text-right">当前最低成本</TableHead>
            <TableHead class="px-6 py-3 text-right">上限价</TableHead>
            <TableHead class="px-6 py-3">货源</TableHead>
            <TableHead class="px-6 py-3">状态</TableHead>
            <TableHead class="px-6 py-3 text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody class="divide-y divide-border">
          <TableRow v-if="loading"><TableCell :colspan="8" class="p-0"><TableSkeleton :columns="8" :rows="4" /></TableCell></TableRow>
          <TableRow v-else-if="products.length === 0"><TableCell :colspan="8" class="px-6 py-8 text-center text-muted-foreground">还没有商品，点「新建商品」开始。</TableCell></TableRow>
          <TableRow v-for="p in products" :key="p.sku_id" class="hover:bg-muted/30">
            <TableCell class="px-6 py-4"><div class="font-medium text-foreground">{{ p.title }}</div><div class="text-xs text-muted-foreground">SKU {{ p.sku_id }}</div></TableCell>
            <TableCell class="px-6 py-4 text-sm">{{ p.mode === 'redeem' ? '发自家卡密，兑换时采购' : '付款即采购' }}</TableCell>
            <TableCell class="px-6 py-4 text-sm">{{ p.rule === 'order' ? '按顺序' : '最便宜' }}</TableCell>
            <TableCell class="px-6 py-4 text-right tabular-nums">{{ yuan(cheapest(p)) }}</TableCell>
            <TableCell class="px-6 py-4 text-right tabular-nums">{{ p.max_unit_cost ? yuan(p.max_unit_cost) : '不限' }}</TableCell>
            <TableCell class="px-6 py-4 text-sm text-muted-foreground">{{ p.sources.filter((s) => s.enabled).length }} 个启用</TableCell>
            <TableCell class="px-6 py-4">
              <span class="inline-flex rounded-full border px-2.5 py-1 text-xs" :class="(HEALTH[p.health] || HEALTH.unknown)!.cls">{{ (HEALTH[p.health] || HEALTH.unknown)!.label }}</span>
            </TableCell>
            <TableCell class="px-6 py-4 text-right"><Button size="sm" variant="outline" @click="openEdit(p)">配置</Button></TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>

    <Dialog v-model:open="showModal">
      <DialogScrollContent class="w-[calc(100vw-1rem)] max-w-3xl p-4 sm:p-6" @interact-outside="(e: Event) => e.preventDefault()">
        <DialogHeader><DialogTitle>{{ form.sku_id ? `配置货源池：${form.title}` : '新建商品' }}</DialogTitle></DialogHeader>
        <form class="space-y-6" @submit.prevent="save">
          <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div class="md:col-span-2">
              <label class="mb-1.5 block text-xs font-medium text-muted-foreground">商品名称</label>
              <Input v-model="form.title" placeholder="例如：GPT Plus 月卡 直充" />
            </div>
            <div>
              <label class="mb-1.5 block text-xs font-medium text-muted-foreground">发货方式</label>
              <Select v-model="form.mode">
                <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="redeem">兑换：卖出发自家卡密，买家兑换时才采购</SelectItem>
                  <SelectItem value="direct">直发：买家付款就采购</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label class="mb-1.5 block text-xs font-medium text-muted-foreground">选源规则</label>
              <Select v-model="form.rule">
                <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="cheapest">有货且不超上限，选总价最低</SelectItem>
                  <SelectItem value="order">按下面的顺序，第一个能买的</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label class="mb-1.5 block text-xs font-medium text-muted-foreground">最高采购价（含手续费，0 为不限）</label>
              <Input v-model.number="form.max_unit_cost" type="number" step="0.01" min="0" />
            </div>
            <div>
              <label class="mb-1.5 block text-xs font-medium text-muted-foreground">报给卡网的成本价</label>
              <Input v-model.number="form.cost" type="number" step="0.01" min="0" />
            </div>
            <template v-if="form.mode === 'redeem'">
              <div>
                <label class="mb-1.5 block text-xs font-medium text-muted-foreground">卡密有效期（天）</label>
                <Input v-model.number="form.valid_days" type="number" min="1" />
              </div>
              <div>
                <label class="mb-1.5 block text-xs font-medium text-muted-foreground">兑换前提醒（分钟内要用，0 为不提醒）</label>
                <Input v-model.number="form.warn_minutes" type="number" min="0" />
              </div>
            </template>
            <div class="md:col-span-2">
              <label class="mb-1.5 block text-xs font-medium text-muted-foreground">给买家的使用说明</label>
              <Textarea v-model="form.instruction" rows="3" />
            </div>
            <div class="flex items-center gap-3 md:col-span-2">
              <Switch id="hide-note" v-model="form.hide_upstream_note" />
              <label for="hide-note" class="text-sm">隐藏上游自带的说明（只给买家看我的说明）</label>
            </div>
          </div>

          <div class="space-y-3">
            <div class="text-sm font-medium">货源</div>
            <div class="overflow-x-auto rounded-lg border border-border">
              <table class="w-full min-w-[640px] text-sm">
                <thead class="bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th class="px-3 py-2 text-left font-medium">启用</th>
                    <th class="px-3 py-2 text-left font-medium">渠道</th>
                    <th class="px-3 py-2 text-left font-medium">货源</th>
                    <th class="px-3 py-2 text-right font-medium">现价</th>
                    <th class="px-3 py-2 text-right font-medium">库存</th>
                    <th class="px-3 py-2 text-left font-medium">备注</th>
                    <th class="px-3 py-2" />
                  </tr>
                </thead>
                <tbody class="divide-y divide-border">
                  <tr v-if="form.sources.length === 0"><td colspan="7" class="px-3 py-4 text-center text-muted-foreground">还没有货源，从下面加。</td></tr>
                  <tr v-for="(s, i) in form.sources" :key="i">
                    <td class="px-3 py-2"><Checkbox :model-value="s.enabled" @update:model-value="(v) => (s.enabled = v === true)" /></td>
                    <td class="px-3 py-2">{{ s.type === '16688' ? '16688' : '人工' }}</td>
                    <td class="px-3 py-2 font-mono text-xs">{{ s.type === '16688' ? s.goods_no : s.label }}</td>
                    <td class="px-3 py-2 text-right tabular-nums">
                      <template v-if="s.type === 'manual'">手填</template>
                      <template v-else-if="s.quote && s.quote.ok">{{ yuan(s.quote.total) }}</template>
                      <span v-else-if="s.quote" class="text-xs text-red-600">{{ s.quote.message || '查不到' }}</span>
                      <template v-else>保存后巡检</template>
                    </td>
                    <td class="px-3 py-2 text-right tabular-nums">
                      <Input v-if="s.type === 'manual'" v-model.number="s.stock" type="number" min="0" class="h-8 w-20 text-right" />
                      <template v-else-if="s.quote && s.quote.ok">{{ s.quote.stock || '缺货' }}</template>
                      <template v-else>—</template>
                    </td>
                    <td class="px-3 py-2"><Input v-model="s.note" class="h-8" placeholder="自己看的备注" /></td>
                    <td class="whitespace-nowrap px-3 py-2 text-right">
                      <Button type="button" size="sm" variant="ghost" :disabled="i === 0" @click="move(i, -1)">↑</Button>
                      <Button type="button" size="sm" variant="ghost" :disabled="i === form.sources.length - 1" @click="move(i, 1)">↓</Button>
                      <Button type="button" size="sm" variant="ghost" class="text-destructive" @click="form.sources.splice(i, 1)">移出</Button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="flex flex-wrap gap-2">
              <Input v-model="newGoods" class="w-56" placeholder="16688 货源编号，如 G24324524" @keydown.enter.prevent="addGoods" />
              <Button type="button" variant="outline" @click="addGoods">加入 16688 货源</Button>
              <Input v-model="newManual.label" class="w-40" placeholder="人工来源名称" />
              <Input v-model.number="newManual.stock" type="number" min="0" class="w-24" placeholder="可售数量" />
              <Button type="button" variant="outline" @click="addManual">加入人工来源</Button>
            </div>
            <p class="text-xs text-muted-foreground">16688 的货源要先在 16688 网页上点「代理」，才能在「货源渠道」里看到编号。人工来源是兜底：自动渠道都买不到时，订单转到「异常与人工」等你手动发。</p>
          </div>

          <div class="flex justify-end gap-2">
            <Button type="button" variant="outline" @click="showModal = false">取消</Button>
            <Button type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</Button>
          </div>
        </form>
      </DialogScrollContent>
    </Dialog>
  </div>
</template>
