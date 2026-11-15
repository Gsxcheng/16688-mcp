<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { procurementAPI, yuan, type Proposal } from '@/api/procurement'
import { confirmAction } from '@/utils/confirm'
import { notifySuccess } from '@/utils/notify'
import ProcurementStats from './ProcurementStats.vue'

const loading = ref(true)
const proposals = ref<Proposal[]>([])
const busy = ref<number | null>(null)
const stats = ref<InstanceType<typeof ProcurementStats> | null>(null)

const open = computed(() => proposals.value.filter((p) => p.status === 'open'))
const done = computed(() => proposals.value.filter((p) => p.status !== 'open').slice(0, 10))

const load = async () => {
  loading.value = true
  try {
    proposals.value = (await procurementAPI.proposals()).proposals
  } catch {
    proposals.value = []
  } finally {
    loading.value = false
  }
}

const margin = (p: Proposal) => (p.price && p.cost ? yuan(p.price - p.cost) : '—')

const decide = async (p: Proposal, action: 'approve' | 'reject') => {
  if (action === 'approve') {
    const what = p.type === 'new_product' ? `新增商品「${p.title}」，加入货源池` : `执行「${p.title}」`
    const ok = await confirmAction({ title: '同意这条提案？', description: `${what}。之后可以在「货源池」里随时改。` })
    if (!ok) return
  }
  busy.value = p.id
  try {
    await procurementAPI.decide(p.id, action)
    notifySuccess(action === 'approve' ? '已同意' : '已拒绝')
    await load()
    stats.value?.load()
  } catch {
    /* already notified */
  } finally {
    busy.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-semibold">待拍板</h1>
        <p class="mt-1 text-sm text-muted-foreground">系统找到的新货源和调价建议都在这里，同意后自动生效。</p>
      </div>
      <Button variant="outline" class="w-full sm:w-auto" :disabled="loading" @click="load">刷新</Button>
    </div>

    <ProcurementStats ref="stats" />

    <div v-if="loading" class="rounded-xl border border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">加载中…</div>
    <div v-else-if="open.length === 0" class="rounded-xl border border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">
      现在没有要你拍板的提案。
    </div>

    <div v-for="p in open" :key="p.id" class="rounded-xl border border-border bg-card">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-6 py-4">
        <div class="flex items-center gap-2">
          <span class="font-medium text-foreground">{{ p.title }}</span>
          <span
            class="inline-flex rounded-full border px-2 py-0.5 text-xs"
            :class="p.type === 'new_product' ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-amber-200 bg-amber-50 text-amber-700'"
          >{{ p.type === 'new_product' ? '新增商品' : '移出货源' }}</span>
          <span v-if="p.mode" class="inline-flex rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {{ p.mode === 'redeem' ? '兑换模式' : '直发模式' }}
          </span>
        </div>
        <span class="text-xs text-muted-foreground">{{ p.created_at }}</span>
      </div>
      <div class="space-y-4 px-6 py-4">
        <div v-if="p.type === 'new_product'" class="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div class="rounded-lg bg-muted/40 px-3 py-2"><div class="text-xs text-muted-foreground">当前最低成本</div><div class="font-semibold tabular-nums">{{ yuan(p.cost) }}</div></div>
          <div class="rounded-lg bg-muted/40 px-3 py-2"><div class="text-xs text-muted-foreground">建议售价</div><div class="font-semibold tabular-nums">{{ yuan(p.price) }}</div></div>
          <div class="rounded-lg bg-muted/40 px-3 py-2"><div class="text-xs text-muted-foreground">预计毛利</div><div class="font-semibold tabular-nums">{{ margin(p) }}</div></div>
          <div class="rounded-lg bg-muted/40 px-3 py-2"><div class="text-xs text-muted-foreground">建议上限价</div><div class="font-semibold tabular-nums">{{ yuan(p.max_unit_cost) }}</div></div>
        </div>
        <div v-if="p.sources && p.sources.length" class="overflow-x-auto rounded-lg border border-border">
          <table class="w-full min-w-[520px] text-sm">
            <thead class="bg-muted/40 text-xs text-muted-foreground">
              <tr><th class="px-4 py-2 text-left font-medium">货源</th><th class="px-4 py-2 text-right font-medium">含手续费</th><th class="px-4 py-2 text-right font-medium">库存</th><th class="px-4 py-2 text-left font-medium">说明</th></tr>
            </thead>
            <tbody class="divide-y divide-border">
              <tr v-for="(s, i) in p.sources" :key="i">
                <td class="px-4 py-2">{{ s.name || s.label || '' }} <span class="font-mono text-xs text-muted-foreground">{{ s.goods_no }}</span></td>
                <td class="px-4 py-2 text-right tabular-nums">{{ yuan(s.total) }}</td>
                <td class="px-4 py-2 text-right tabular-nums">{{ s.stock ?? '—' }}</td>
                <td class="px-4 py-2 text-muted-foreground">{{ s.note || '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="p.reason" class="text-sm"><span class="text-muted-foreground">理由：</span>{{ p.reason }}</p>
        <p v-if="p.risk" class="text-sm text-amber-700"><span>要注意：</span>{{ p.risk }}</p>
        <div class="flex gap-2">
          <Button :disabled="busy === p.id" @click="decide(p, 'approve')">同意</Button>
          <Button variant="outline" :disabled="busy === p.id" @click="decide(p, 'reject')">不要</Button>
        </div>
      </div>
    </div>

    <div v-if="done.length" class="rounded-xl border border-border bg-card px-6 py-4">
      <div class="mb-2 text-sm font-medium">最近处理过的</div>
      <div v-for="p in done" :key="p.id" class="flex items-center gap-2 py-1 text-sm">
        <span class="text-xs text-muted-foreground">{{ p.created_at }}</span>
        <span>{{ p.title }}</span>
        <span
          class="inline-flex rounded-full border px-2 py-0.5 text-xs"
          :class="p.status === 'approved' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-border bg-muted text-muted-foreground'"
        >{{ p.status === 'approved' ? '已同意' : '已拒绝' }}</span>
      </div>
    </div>
  </div>
</template>
