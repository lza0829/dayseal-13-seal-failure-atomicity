<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const preview = ref(null)
const declared = ref(0)
const batches = ref([])
const msg = ref('')
const err = ref('')

async function load() {
  preview.value = await api.closePreview()
  declared.value = preview.value.system_total
  batches.value = await api.batches()
}

async function seal() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.seal({
      business_day: preview.value.business_day,
      declared_cash: declared.value,
    })
    msg.value = `已封账：系统 ${r.system_total.toFixed(2)} / 实盘 ${r.declared_cash.toFixed(2)} / 长短款 ${r.variance.toFixed(2)}`
    await load()
  } catch (e) { err.value = e.message }
}

onMounted(load)
</script>

<template>
  <h2>日结封账</h2>
  <div v-if="preview" class="panel">
    <p>营业日 {{ preview.business_day }} · 笔数 {{ preview.line_count }} · 系统合计 {{ preview.system_total.toFixed(2) }}
      <span class="tag" :class="preview.sealed ? 'ok' : 'warn'">{{ preview.sealed ? '已封' : '未封' }}</span>
    </p>
    <div class="row">
      <label>实盘现金 <input type="number" step="0.01" v-model.number="declared" /></label>
      <button class="btn" :disabled="preview.sealed" @click="seal">封账</button>
      <button class="btn ghost" @click="load">刷新</button>
    </div>
  </div>
  <p v-if="msg" class="msg">{{ msg }}</p>
  <p v-if="err" class="msg err">{{ err }}</p>
  <div class="panel">
    <h2>封账批次</h2>
    <table>
      <thead><tr><th>批次</th><th>营业日</th><th>系统</th><th>实盘</th><th>差额</th><th>状态</th></tr></thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td><td>{{ b.business_day }}</td>
          <td>{{ b.system_total.toFixed(2) }}</td><td>{{ b.declared_cash.toFixed(2) }}</td>
          <td>{{ b.variance.toFixed(2) }}</td><td>{{ b.status }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
