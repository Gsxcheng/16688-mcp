<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogScrollContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { procurementAPI, statusOf, type ProcOrder } from '@/api/procurement'
import { confirmAction } from '@/utils/confirm'
import { notifyError, notifySuccess } from '@/utils/notify'

const loading = ref(true)
const orders = ref<ProcOrder[]>([])
const busy = ref('')
const deliver = reactive({ open: false, order: null as ProcOrder | null, payload: '' })

const HINT: Record<string, string> = {
  waiting_stock: '货源池里暂时没有能买的，系统每 5 分钟自动重试，买到会自动发给买家。也可以手动交付。',
  awaiting_manual: '已转人工来源。你买好后把卡密粘贴进来交付。',
  needs_review: '向 16688 下单时超时或出错，不确定有没有扣款。先去 16688 采购记录核对：没扣款就点「重新采购」，已扣款就把卡密粘贴交付。',
  processing: '上游正在发货，一般几分钟内完成。',
}

const load = async () => {
  loading.value = true
  try {
    orders.value = (await procurementAPI.exceptions()).orders
  } catch {
    orders.value = []
  } finally {
    loading.value = false
  }
}

const act = async (o: ProcOrder, action: 'cancel' | 'retry') => {
  const ok = await confirmAction(
    action === 'cancel'
      ? { title: '取消这一单？', description: '买家会看到「已取消，请联系售后退款」。退款需要你另外处理。', variant: 'destructive' }
      : { title: '重新采购？', description: '请确认你已在 16688 核对过：上一次没有扣款。确认后系统会重新比价下单。' },
  )
  if (!ok) return
  busy.value = o.order_no
  try {
    const r = await procurementAPI.orderAction(action, o.order_no)
    notifySuccess(action === 'cancel' ? '已取消' : `已重新采购：${statusOf(r.order.status).label}`)
    await load()
  } catch {
    /* notified */
  } finally {
    busy.value = ''
  }
}

const openDeliver = (o: ProcOrder) => {
  deliver.order = o
  deliver.payload = ''
  deliver.open = true
}

const submitDeliver = async () => {
  if (!deliver.order) return
  if (!deliver.payload.trim()) {
    notifyError('请粘贴要交付给买家的内容')
    return
  }
  busy.value = deliver.order.order_no
  try {
    await procurementAPI.orderAction('deliver', deliver.order.order_no, { payload: deliver.payload })
    notifySuccess('已交付，买家马上能看到')
    deliver.open = false
    await load()
  } catch {
    /* notified */
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-semibold">异常与人工</h1>
        <p class="mt-1 text-sm text-muted-foreground">只有系统处理不了的单才会出现在这里。</p>
      </div>
      <Button variant="outline" class="w-full sm:w-auto" :disabled="loading" @click="load">刷新</Button>
    </div>

    <div v-if="loading" class="rounded-xl border border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">加载中…</div>
    <div v-else-if="orders.length === 0" class="rounded-xl border border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">没有要处理的订单。</div>

    <div v-for="o in orders" :key="o.order_no" class="rounded-xl border border-border bg-card">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-6 py-4">
        <div class="font-medium">{{ o.product }} × {{ o.quantity }}</div>
        <span class="inline-flex rounded-full border px-2.5 py-1 text-xs" :class="statusOf(o.status).cls">{{ statusOf(o.status).label }}</span>
      </div>
      <div class="space-y-3 px-6 py-4 text-sm">
        <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span class="font-mono">{{ o.order_no }}</span>
          <span>{{ o.created_at }}</span>
          <span>来自{{ o.from }}</span>
          <span v-if="o.code">卡密 <span class="font-mono">{{ o.code }}</span></span>
          <span v-if="o.supplier_ref">货源单号 <span class="font-mono">{{ o.supplier_ref }}</span></span>
        </div>
        <p>{{ HINT[o.status] || '' }}</p>
        <p v-if="o.error" class="text-xs text-muted-foreground">系统记录：{{ o.error }}</p>
        <div class="flex flex-wrap gap-2">
          <Button size="sm" :disabled="busy === o.order_no" @click="openDeliver(o)">粘贴卡密交付</Button>
          <Button v-if="o.status === 'needs_review'" size="sm" variant="outline" :disabled="busy === o.order_no" @click="act(o, 'retry')">确认没扣款，重新采购</Button>
          <Button size="sm" variant="outline" class="text-destructive" :disabled="busy === o.order_no" @click="act(o, 'cancel')">取消</Button>
        </div>
      </div>
    </div>

    <Dialog v-model:open="deliver.open">
      <DialogScrollContent class="w-[calc(100vw-1rem)] max-w-lg p-4 sm:p-6">
        <DialogHeader><DialogTitle>交付 {{ deliver.order?.product }}</DialogTitle></DialogHeader>
        <div class="space-y-4">
          <Textarea v-model="deliver.payload" rows="6" class="font-mono text-sm" placeholder="粘贴卡密或使用说明，买家会原样看到" />
          <div class="flex justify-end gap-2">
            <Button variant="outline" @click="deliver.open = false">取消</Button>
            <Button :disabled="!!busy" @click="submitDeliver">交付</Button>
          </div>
        </div>
      </DialogScrollContent>
    </Dialog>
  </div>
</template>
