<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { Dialog, DialogScrollContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import TableSkeleton from '@/components/TableSkeleton.vue'
import { procurementAPI, statusOf, yuan, VERDICTS, type ProcOrder } from '@/api/procurement'

const loading = ref(true)
const orders = ref<ProcOrder[]>([])
const picked = ref<ProcOrder | null>(null)

const load = async () => {
  loading.value = true
  try {
    orders.value = (await procurementAPI.log()).orders
  } catch {
    orders.value = []
  } finally {
    loading.value = false
  }
}

const verdictCls = (v: string) =>
  v === 'chosen' ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
    : v === 'candidate' ? 'border-blue-200 bg-blue-50 text-blue-700'
      : 'border-border bg-muted text-muted-foreground'

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-semibold">采购记录</h1>
        <p class="mt-1 text-sm text-muted-foreground">最近 50 单。点「怎么选的」看每次比价的过程。</p>
      </div>
      <Button variant="outline" class="w-full sm:w-auto" :disabled="loading" @click="load">刷新</Button>
    </div>

    <div class="rounded-xl border border-border bg-card overflow-x-auto">
      <Table class="min-w-[900px]">
        <TableHeader class="border-b border-border bg-muted/40 text-xs uppercase text-muted-foreground">
          <TableRow>
            <TableHead class="px-6 py-3">时间</TableHead>
            <TableHead class="px-6 py-3">商品</TableHead>
            <TableHead class="px-6 py-3">来自</TableHead>
            <TableHead class="px-6 py-3">货源单号</TableHead>
            <TableHead class="px-6 py-3 text-right">成本</TableHead>
            <TableHead class="px-6 py-3">状态</TableHead>
            <TableHead class="px-6 py-3 text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody class="divide-y divide-border">
          <TableRow v-if="loading"><TableCell :colspan="7" class="p-0"><TableSkeleton :columns="7" :rows="6" /></TableCell></TableRow>
          <TableRow v-else-if="orders.length === 0"><TableCell :colspan="7" class="px-6 py-8 text-center text-muted-foreground">还没有采购记录</TableCell></TableRow>
          <TableRow v-for="o in orders" :key="o.order_no" class="hover:bg-muted/30">
            <TableCell class="px-6 py-3 text-xs text-muted-foreground">{{ o.created_at }}</TableCell>
            <TableCell class="px-6 py-3">
              <div class="text-foreground">{{ o.product }} × {{ o.quantity }}</div>
              <div v-if="o.code" class="font-mono text-xs text-muted-foreground">{{ o.code }}</div>
            </TableCell>
            <TableCell class="px-6 py-3 text-sm">{{ o.from }}</TableCell>
            <TableCell class="px-6 py-3 font-mono text-xs">{{ o.supplier_ref || '—' }}</TableCell>
            <TableCell class="px-6 py-3 text-right tabular-nums">{{ o.amount ? yuan(Number(o.amount)) : '—' }}</TableCell>
            <TableCell class="px-6 py-3"><span class="inline-flex rounded-full border px-2.5 py-1 text-xs" :class="statusOf(o.status).cls">{{ statusOf(o.status).label }}</span></TableCell>
            <TableCell class="px-6 py-3 text-right"><Button size="sm" variant="outline" :disabled="!o.trace.length" @click="picked = o">怎么选的</Button></TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>

    <Dialog :open="!!picked" @update:open="(v: boolean) => { if (!v) picked = null }">
      <DialogScrollContent class="w-[calc(100vw-1rem)] max-w-xl p-4 sm:p-6">
        <DialogHeader><DialogTitle>{{ picked?.product }} 的比价过程</DialogTitle></DialogHeader>
        <div v-if="picked" class="space-y-3">
          <p class="text-xs text-muted-foreground">{{ picked.order_no }} · {{ picked.created_at }} · 价格含手续费</p>
          <div class="overflow-x-auto rounded-lg border border-border">
            <table class="w-full text-sm">
              <thead class="bg-muted/40 text-xs text-muted-foreground">
                <tr><th class="px-3 py-2 text-left font-medium">货源</th><th class="px-3 py-2 text-right font-medium">总价</th><th class="px-3 py-2 text-right font-medium">库存</th><th class="px-3 py-2 text-left font-medium">结果</th></tr>
              </thead>
              <tbody class="divide-y divide-border">
                <tr v-for="(t, i) in picked.trace" :key="i">
                  <td class="px-3 py-2 font-mono text-xs">{{ t.src }}</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ yuan(t.total) }}</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ t.stock ?? '—' }}</td>
                  <td class="px-3 py-2"><span class="inline-flex rounded-full border px-2 py-0.5 text-xs" :class="verdictCls(t.verdict)">{{ VERDICTS[t.verdict] || t.verdict }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </DialogScrollContent>
    </Dialog>
  </div>
</template>
