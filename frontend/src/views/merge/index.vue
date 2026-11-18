<template>
  <section class="page" data-module="merge">
    <header class="page-head">
      <div>
        <h2>结果聚合归并管理</h2>
        <p class="page-desc">维护采集结果的聚合策略、进度追踪与输出管理，为结果聚合归并提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记聚合任务</button>
        <button class="btn" type="button" @click="exportRows">导出结果聚合归并清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无结果聚合归并数据，可先登记聚合任务</td>
        </tr>
      </tbody>
    </table>


    <section class="archive-panel">
      <header class="panel-head">
        <div>
          <h3>新增存储分层准入待处理队列</h3>
          <p class="page-desc">分层归档结果写入这里；结果聚合只引用同一策略版本下的可用层级。</p>
        </div>
        <div class="page-actions">
          <label class="filter-item">
            <span>策略版本</span>
            <select v-model="policyVersion" data-testid="merge-policy-version">
              <option v-for="version in policyVersions" :key="version" :value="version">{{ version }}</option>
            </select>
          </label>
          <button class="btn primary" type="button" @click="syncStorage">同步分层准入清单</button>
        </div>
      </header>

      <div class="stat-row">
        <article v-for="item in storageStats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>
      <p v-if="storageMessage" class="action-message" data-testid="merge-message">{{ storageMessage }}</p>

      <h4>待处理准入队列</h4>
      <table class="data-table" data-testid="merge-admission">
        <thead>
          <tr>
            <th>准入编号</th><th>归档编号</th><th>文件编号</th><th>策略版本</th>
            <th>归档层级</th><th>准入结果</th><th>归档状态</th><th>清理依据</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in admissionRows" :key="keyOf(row, '准入编号')">
            <td>{{ row['准入编号'] }}</td>
            <td>{{ row['归档编号'] }}</td>
            <td>{{ row['文件编号'] }}</td>
            <td>{{ row['策略版本'] }}</td>
            <td>{{ row['归档层级'] || '—' }}</td>
            <td>{{ row['准入结果'] }}</td>
            <td>{{ row['归档状态'] }}</td>
            <td>{{ row['清理依据'] || '—' }}</td>
            <td>{{ row['状态'] }}</td>
          </tr>
          <tr v-if="!admissionRows.length">
            <td colspan="9" class="empty-state">暂无待处理准入记录，可先在采集存储分层执行归档</td>
          </tr>
        </tbody>
      </table>

      <h4>归档结果引用（同一策略版本可用层级）</h4>
      <table class="data-table" data-testid="merge-archive-reference">
        <thead>
          <tr>
            <th>归档编号</th><th>文件编号</th><th>策略版本</th><th>归档层级</th>
            <th>保留天数</th><th>到期日期</th><th>清理依据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in referenceRows" :key="keyOf(row, '归档编号')">
            <td>{{ row['归档编号'] }}</td>
            <td>{{ row['文件编号'] }}</td>
            <td>{{ row['策略版本'] }}</td>
            <td>{{ row['归档层级'] }}</td>
            <td>{{ row['保留天数'] }}</td>
            <td>{{ row['到期日期'] || '—' }}</td>
            <td>{{ row['清理依据'] || '—' }}</td>
          </tr>
          <tr v-if="!referenceRows.length">
            <td colspan="7" class="empty-state">当前策略版本下没有可引用的归档层级</td>
          </tr>
        </tbody>
      </table>

      <h4>按层级与清理依据聚合</h4>
      <table class="data-table" data-testid="merge-archive-aggregate">
        <thead>
          <tr><th>归档层级</th><th>引用数量</th><th>策略编号</th></tr>
        </thead>
        <tbody>
          <tr v-for="tier in referenceTiers" :key="keyOf(tier, '归档层级')">
            <td>{{ tier['归档层级'] }}</td>
            <td>{{ tier['数量'] }}</td>
            <td>{{ tier['策略编号'] || '—' }}</td>
          </tr>
          <tr v-for="basis in referenceBasis" :key="`basis-${keyOf(basis, '清理依据')}`">
            <td colspan="2">{{ basis['清理依据'] }}</td>
            <td>{{ basis['数量'] }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条结果聚合归并记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/merge'
const columns = ["任务编号", "采集计划", "分片总数", "已归并数", "归并策略", "输出路径", "数据总量", "任务状态"]
const actions = ["启动归并", "重试归并", "归档任务"]
const statuses = ["待归并", "归并中", "已完成", "归并失败"]
const stats = [{"label": "聚合任务总数", "value": 0}, {"label": "归并中任务", "value": 0}, {"label": "已完成任务", "value": 0}]

const policyVersions = ref<string[]>([])
const policyVersion = ref('')
const admissionRows = ref<Row[]>([])
const referenceRows = ref<Row[]>([])
const referenceTiers = ref<Row[]>([])
const referenceBasis = ref<Row[]>([])
const storageMessage = ref('')
const storageStats = ref([
  { label: '准入队列', value: 0 },
  { label: '待处理', value: 0 },
  { label: '可引用层级', value: 0 },
])

function keyOf(row: Row, field: string): string {
  return String(row[field] ?? '')
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '聚合任务登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('结果聚合归并动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结果聚合归并操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('聚合任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结果聚合归并列表读取失败'
  }
}

async function syncStorage() {
  storageMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/storage-sync`, {
      method: 'POST',
      body: JSON.stringify({ 策略版本: policyVersion.value }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.detail || payload.message || '同步分层准入清单失败')
    }
    storageMessage.value = payload.message ?? '分层准入清单已刷新'
    await loadStorageView()
  } catch (error) {
    storageMessage.value = error instanceof Error ? error.message : '同步分层准入清单失败'
  }
}

async function loadStorageView() {
  const query = policyVersion.value ? `policy_version=${encodeURIComponent(policyVersion.value)}` : ''
  const [admissionPayload, referencePayload] = await Promise.all([
    fetchJson(`${ENDPOINT}/storage-admission?${query}`),
    fetchJson(`${ENDPOINT}/storage-archive?${query}`),
  ])
  policyVersions.value = admissionPayload.policy_versions ?? []
  if (!policyVersion.value && policyVersions.value.length) {
    policyVersion.value = policyVersions.value[policyVersions.value.length - 1]
  }
  admissionRows.value = admissionPayload.items ?? []
  referenceRows.value = referencePayload.items ?? []
  referenceTiers.value = referencePayload.tiers ?? []
  referenceBasis.value = referencePayload.cleanup_basis ?? []
  storageStats.value = [
    { label: '准入队列', value: admissionPayload.total ?? admissionRows.value.length },
    {
      label: '待处理',
      value: admissionRows.value.filter((item) => item['状态'] === '待处理').length,
    },
    { label: '可引用层级', value: (referencePayload.usable_tiers ?? []).length },
  ]
}

async function fetchJson(path: string) {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return response.json()
}

async function bootstrap() {
  await reload()
  try {
    await loadStorageView()
  } catch (error) {
    storageMessage.value = error instanceof Error ? error.message : '分层准入数据读取失败'
  }
}

onMounted(bootstrap)
</script>

<style scoped>
.archive-panel { margin-top: 24px; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.action-message { font-size: 13px; color: var(--brand); }
h4 { margin: 18px 0 8px; font-size: 14px; }
select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: #fff; }
</style>
