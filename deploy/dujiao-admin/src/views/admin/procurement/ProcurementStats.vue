<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { procurementAPI, yuan, type Overview } from '@/api/procurement'

const ov = ref<Overview | null>(null)
const modeTitle = computed(() => {
  const m = ov.value?.purchase_mode
  if (m === 'mock') return '模拟上游'
  if (m === 'formal') return '正式采购'
  if (m === 'formal_blocked') return '正式已拦截'
  if (m === 'simulated' || ov.value) return '模拟'
  return '—'
})
const modeHint = computed(() => (ov.value?.purchase_mode === 'formal' ? '已单独授权' : '不发生真实付款'))

const load = async () => {
  try {
    ov.value = await procurementAPI.overview()
  } catch {
    ov.value = null
  }
}

defineExpose({ load })
onMounted(load)
</script>

<template>
  <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
    <div class="rounded-xl border border-border bg-card px-4 py-3">
      <div class="text-xs text-muted-foreground">16688 运营钱包</div>
      <div class="mt-1 flex items-center gap-2">
        <span class="text-xl font-semibold tabular-nums">{{ ov ? yuan(ov.wallet) : '—' }}</span>
        <span
          v-if="ov && ov.wallet !== null"
          class="inline-flex rounded-full border px-2 py-0.5 text-xs"
          :class="ov.wallet > ov.min_balance ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'"
        >{{ ov.wallet > ov.min_balance ? '够用' : '需充值' }}</span>
      </div>
    </div>
    <div class="rounded-xl border border-border bg-card px-4 py-3">
      <div class="text-xs text-muted-foreground">今日采购</div>
      <div class="mt-1 text-xl font-semibold tabular-nums">
        {{ ov ? ov.orders_today : '—' }} <span class="text-sm font-normal text-muted-foreground">/ 上限 {{ ov?.max_orders ?? '—' }}</span>
      </div>
    </div>
    <div class="rounded-xl border border-border bg-card px-4 py-3">
      <div class="text-xs text-muted-foreground">未兑换的自家卡密</div>
      <div class="mt-1 text-xl font-semibold tabular-nums">{{ ov ? ov.unredeemed : '—' }}</div>
    </div>
    <div class="rounded-xl border border-border bg-card px-4 py-3">
      <div class="text-xs text-muted-foreground">采购模式</div>
      <div class="mt-1 flex items-center gap-2">
        <span class="text-xl font-semibold">{{ modeTitle }}</span>
        <span
          v-if="ov"
          class="inline-flex rounded-full border px-2 py-0.5 text-xs"
          :class="ov.purchase_mode === 'formal' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
        >{{ modeHint }}</span>
      </div>
    </div>
  </div>
</template>
