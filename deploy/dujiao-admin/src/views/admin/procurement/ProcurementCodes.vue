<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import TableSkeleton from '@/components/TableSkeleton.vue'
import { procurementAPI, type OwnCode, type PoolProduct } from '@/api/procurement'
import { confirmAction } from '@/utils/confirm'
import { notifyError, notifySuccess } from '@/utils/notify'

const loading = ref(true)
const codes = ref<OwnCode[]>([])
const products = ref<PoolProduct[]>([])
const sku = ref('')
const count = ref(5)
const generated = ref<string[]>([])
const generating = ref(false)
const redeemUrl = ref('')

const redeemable = computed(() => products.value.filter((p) => p.mode === 'redeem'))

const STATE: Record<string, { label: string; cls: string }> = {
  ready: { label: '未兑换', cls: 'border-blue-200 bg-blue-50 text-blue-700' },
  delivered: { label: '已兑换', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  processing: { label: '处理中', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  expired: { label: '已过期', cls: 'border-border bg-muted text-muted-foreground' },
  voided: { label: '已作废', cls: 'border-border bg-muted text-muted-foreground' },
}
const stateOf = (s: string) => STATE[s] || { label: s, cls: 'border-border bg-muted text-muted-foreground' }
const CHANNEL: Record<string, string> = { dujiao: '卡网订单', batch: '批量生成' }

const load = async () => {
  loading.value = true
  try {
    const [c, p, o] = await Promise.all([procurementAPI.codes(), procurementAPI.pools(), procurementAPI.overview()])
    codes.value = c.codes
    products.value = p.products
    redeemUrl.value = o.redeem_url
    if (!sku.value && redeemable.value.length) sku.value = String(redeemable.value[0]!.sku_id)
  } catch {
    codes.value = []
  } finally {
    loading.value = false
  }
}

const generate = async () => {
  const n = Number(count.value)
  if (!sku.value || !(n >= 1 && n <= 200)) {
    notifyError('选一个兑换模式的商品，数量 1 到 200')
    return
  }
  generating.value = true
  try {
    generated.value = (await procurementAPI.generateCodes(Number(sku.value), n)).codes
    notifySuccess(`已生成 ${generated.value.length} 个卡密`)
    await load()
  } catch {
    /* notified */
  } finally {
    generating.value = false
  }
}

const copy = async () => {
  try {
    await navigator.clipboard.writeText(generated.value.join('\n'))
    notifySuccess('已复制')
  } catch {
    notifyError('复制失败，请手动选中复制')
  }
}

const voidCode = async (c: OwnCode) => {
  const ok = await confirmAction({ title: '作废这个卡密？', description: `${c.code} 作废后买家无法兑换，不能恢复。`, variant: 'destructive' })
  if (!ok) return
  try {
    await procurementAPI.voidCode(c.code)
    notifySuccess('已作废')
    await load()
  } catch {
    /* notified */
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold">自家卡密</h1>
      <p class="mt-1 text-sm text-muted-foreground">兑换模式的商品卖出时自动发卡密；也可以在这里批量生成，导入闲鱼助手等渠道。生成不花钱，买家兑换时才采购。兑换页：<span class="font-mono">{{ redeemUrl || '—' }}</span></p>
    </div>

    <div class="rounded-xl border border-border bg-card px-6 py-4 space-y-4">
      <div class="text-sm font-medium">批量生成</div>
      <div class="flex flex-wrap items-end gap-2">
        <div class="w-64">
          <label class="mb-1.5 block text-xs font-medium text-muted-foreground">商品</label>
          <Select v-model="sku">
            <SelectTrigger class="w-full"><SelectValue placeholder="选择兑换模式的商品" /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="p in redeemable" :key="p.sku_id" :value="String(p.sku_id)">{{ p.title }}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div class="w-28">
          <label class="mb-1.5 block text-xs font-medium text-muted-foreground">数量</label>
          <Input v-model.number="count" type="number" min="1" max="200" />
        </div>
        <Button :disabled="generating || !redeemable.length" @click="generate">{{ generating ? '生成中…' : '生成' }}</Button>
      </div>
      <p v-if="!redeemable.length && !loading" class="text-xs text-muted-foreground">还没有兑换模式的商品，先到「货源池」把商品的发货方式改成「兑换」。</p>
      <div v-if="generated.length" class="space-y-2">
        <Textarea :model-value="generated.join('\n')" readonly rows="6" class="font-mono text-xs" />
        <Button size="sm" variant="outline" @click="copy">复制全部</Button>
      </div>
    </div>

    <div class="rounded-xl border border-border bg-card overflow-x-auto">
      <Table class="min-w-[860px]">
        <TableHeader class="border-b border-border bg-muted/40 text-xs uppercase text-muted-foreground">
          <TableRow>
            <TableHead class="px-6 py-3">卡密</TableHead>
            <TableHead class="px-6 py-3">商品</TableHead>
            <TableHead class="px-6 py-3">来源</TableHead>
            <TableHead class="px-6 py-3">发出</TableHead>
            <TableHead class="px-6 py-3">有效期到</TableHead>
            <TableHead class="px-6 py-3">状态</TableHead>
            <TableHead class="px-6 py-3 text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody class="divide-y divide-border">
          <TableRow v-if="loading"><TableCell :colspan="7" class="p-0"><TableSkeleton :columns="7" :rows="6" /></TableCell></TableRow>
          <TableRow v-else-if="codes.length === 0"><TableCell :colspan="7" class="px-6 py-8 text-center text-muted-foreground">还没有卡密</TableCell></TableRow>
          <TableRow v-for="c in codes" :key="c.code" class="hover:bg-muted/30">
            <TableCell class="px-6 py-3 font-mono text-xs">{{ c.code }}</TableCell>
            <TableCell class="px-6 py-3 text-sm">{{ c.product }}</TableCell>
            <TableCell class="px-6 py-3 text-sm">{{ CHANNEL[c.channel] || c.channel }}</TableCell>
            <TableCell class="px-6 py-3 text-xs text-muted-foreground">{{ c.issued_at }}</TableCell>
            <TableCell class="px-6 py-3 text-xs text-muted-foreground">{{ c.expires_at }}</TableCell>
            <TableCell class="px-6 py-3"><span class="inline-flex rounded-full border px-2.5 py-1 text-xs" :class="stateOf(c.state).cls">{{ stateOf(c.state).label }}</span></TableCell>
            <TableCell class="px-6 py-3 text-right">
              <Button v-if="c.state === 'ready' || c.state === 'expired'" size="sm" variant="outline" class="text-destructive" @click="voidCode(c)">作废</Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>
  </div>
</template>
