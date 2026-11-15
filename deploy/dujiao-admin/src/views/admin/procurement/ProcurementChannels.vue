<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import TableSkeleton from '@/components/TableSkeleton.vue'
import { procurementAPI, yuan, type Goods } from '@/api/procurement'

const loading = ref(true)
const goods = ref<Goods[]>([])
const at = ref('')
const configured = ref(true)
const keyword = ref('')

const shown = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  return k ? goods.value.filter((g) => `${g.name} ${g.goods_no}`.toLowerCase().includes(k)) : goods.value
})

const load = async (refresh = false) => {
  loading.value = true
  try {
    const d = await procurementAPI.channel(refresh)
    goods.value = d.goods
    at.value = d.at
    configured.value = d.configured
  } catch {
    goods.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold">货源渠道</h1>
      <p class="mt-1 text-sm text-muted-foreground">货源从哪里看、去哪里买。现在有两个渠道。</p>
    </div>

    <div class="rounded-xl border border-border bg-card">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-6 py-4">
        <div class="flex items-center gap-2">
          <span class="font-medium">16688</span>
          <span
            class="inline-flex rounded-full border px-2 py-0.5 text-xs"
            :class="configured ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'"
          >{{ configured ? '已接通，全自动' : '未配置接口凭据' }}</span>
        </div>
        <div class="flex items-center gap-2">
          <Input v-model="keyword" class="h-9 w-48" placeholder="搜名称或编号" />
          <Button variant="outline" :disabled="loading" @click="load(true)">刷新货源列表</Button>
        </div>
      </div>
      <div class="grid grid-cols-1 gap-3 px-6 py-4 md:grid-cols-3">
        <div class="rounded-lg bg-muted/40 px-3 py-2 text-sm"><div class="text-xs text-muted-foreground">去哪里看货</div>接口读取已代理的货源；新货源要先在 16688 网页上点「代理」</div>
        <div class="rounded-lg bg-muted/40 px-3 py-2 text-sm"><div class="text-xs text-muted-foreground">怎么查价</div>询价接口实时返回货款、手续费和库存</div>
        <div class="rounded-lg bg-muted/40 px-3 py-2 text-sm"><div class="text-xs text-muted-foreground">怎么采购</div>下单接口，从 16688 运营钱包扣款</div>
      </div>
      <div class="overflow-x-auto border-t border-border">
        <Table class="min-w-[760px]">
          <TableHeader class="border-b border-border bg-muted/40 text-xs uppercase text-muted-foreground">
            <TableRow>
              <TableHead class="px-6 py-3">货源</TableHead>
              <TableHead class="px-6 py-3 text-right">货款（不含手续费）</TableHead>
              <TableHead class="px-6 py-3 text-right">库存</TableHead>
              <TableHead class="px-6 py-3 text-right">销量</TableHead>
              <TableHead class="px-6 py-3">在哪个池子</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody class="divide-y divide-border">
            <TableRow v-if="loading"><TableCell :colspan="5" class="p-0"><TableSkeleton :columns="5" :rows="6" /></TableCell></TableRow>
            <TableRow v-else-if="shown.length === 0"><TableCell :colspan="5" class="px-6 py-8 text-center text-muted-foreground">没有货源</TableCell></TableRow>
            <TableRow v-for="g in shown" :key="g.goods_no" class="hover:bg-muted/30">
              <TableCell class="px-6 py-3"><div class="text-foreground">{{ g.name }}</div><div class="font-mono text-xs text-muted-foreground">{{ g.goods_no }}</div></TableCell>
              <TableCell class="px-6 py-3 text-right tabular-nums">{{ yuan(g.unit_price) }}</TableCell>
              <TableCell class="px-6 py-3 text-right tabular-nums">
                <span v-if="!g.available_quantity" class="inline-flex rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">缺货</span>
                <template v-else>{{ g.available_quantity }}</template>
              </TableCell>
              <TableCell class="px-6 py-3 text-right tabular-nums">{{ g.sales_count }}</TableCell>
              <TableCell class="px-6 py-3">
                <span v-if="g.in_pool" class="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">{{ g.in_pool }}</span>
                <span v-else class="text-xs text-muted-foreground">未使用</span>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
      <div class="border-t border-border px-6 py-3 text-xs text-muted-foreground">共 {{ goods.length }} 个，读取于 {{ at || '—' }}</div>
    </div>

    <div class="rounded-xl border border-border bg-card px-6 py-4">
      <div class="flex items-center gap-2">
        <span class="font-medium">人工渠道（Telegram 群等）</span>
        <span class="inline-flex rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-700">采购靠你</span>
      </div>
      <p class="mt-2 text-sm text-muted-foreground">没有接口的货源，在货源池里加一条「人工来源」，填名称和可售数量。自动渠道都买不到时，订单转到「异常与人工」，你买好后把卡密粘贴进去交付。</p>
    </div>
  </div>
</template>
