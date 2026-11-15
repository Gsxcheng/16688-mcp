<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { procurementAPI, yuan, type Overview } from '@/api/procurement'

const ov = ref<Overview | null>(null)

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
        <span class="text-xl font-semibold">{{ ov ? (ov.live ? '真实采购' : '模拟') : '—' }}</span>
        <span
          v-if="ov"
          class="inline-flex rounded-full border px-2 py-0.5 text-xs"
          :class="ov.live ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
        >{{ ov.live ? '会扣钱包' : '不扣钱' }}</span>
      </div>
    </div>
  </div>
</template>
