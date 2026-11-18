<template>
  <section class="page" data-module="storage">
    <header class="page-head">
      <div>
        <h2>采集存储分层管理</h2>
        <p class="page-desc">维护采集存储的分层配置、格式选择与迁移规则，为采集存储分层管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记存储策略</button>
        <button class="btn" type="button" @click="exportRows">导出采集存储分层清单</button>
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
        <tr v-for="row in rows" :key="keyOf(row, 'id')">
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
          <td :colspan="columns.length + 1" class="empty-state">暂无采集存储分层数据，可先登记存储策略</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集存储分层记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="archive-panel">
      <header class="panel-head">
        <div>
          <h3>分层归档执行</h3>
          <p class="page-desc">一次归档只使用一个策略版本；结果进入聚合模块的新增存储分层准入待处理队列。</p>
        </div>
        <span class="policy-hint">策略版本：{{ policyVersions.join(' / ') || '—' }}</span>
      </header>

      <div class="filter-bar">
        <label class="filter-item">
          <span>策略版本</span>
          <select v-model="archiveForm.policyVersion" data-testid="storage-policy-version">
            <option v-for="version in policyVersions" :key="version" :value="version">{{ version }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>终态优先级</span>
          <select v-model="archiveForm.precedence" data-testid="storage-precedence">
            <option value="恢复终态优先">恢复终态优先</option>
            <option value="清理终态优先">清理终态优先</option>
          </select>
        </label>
        <button class="btn primary" type="button" @click="runArchive">执行分层归档</button>
        <button class="btn" type="button" @click="runScan">扫描存储文件</button>
        <button class="btn" type="button" @click="runRebuildCleanup">重建清理清单</button>
        <button class="btn" type="button" @click="runBackfill">历史回填</button>
        <button class="btn ghost" type="button" @click="syncMerge">同步聚合准入</button>
      </div>

      <div class="stat-row">
        <article v-for="item in archiveStats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>
      <p v-if="actionMessage" class="action-message" data-testid="storage-message">{{ actionMessage }}</p>

      <h4>归档页文件扫描结果</h4>
      <table class="data-table" data-testid="storage-files">
        <thead>
          <tr>
            <th>文件编号</th><th>文件名</th><th>格式</th><th>大小</th>
            <th>空文件层级</th><th>归档层级</th><th>准入结果</th>
            <th>保留天数</th><th>到期日期</th><th>恢复引用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="file in files" :key="keyOf(file, '文件编号')">
            <td>{{ file['文件编号'] }}</td>
            <td>{{ file['文件名'] }}</td>
            <td>{{ file['文件格式'] }}</td>
            <td>{{ file['文件大小'] }}</td>
            <td>{{ file['空文件层级'] || '—' }}</td>
            <td>{{ file['归档层级'] ?? '—' }}</td>
            <td>{{ file['准入结果'] }}</td>
            <td>{{ file['保留天数'] }}</td>
            <td>{{ file['到期日期'] || '—' }}</td>
            <td>{{ file['恢复引用显示'] }}</td>
          </tr>
          <tr v-if="!files.length">
            <td colspan="10" class="empty-state">暂无扫描结果，可先扫描存储文件</td>
          </tr>
        </tbody>
      </table>

      <h4>分层归档结果</h4>
      <table class="data-table" data-testid="storage-archive">
        <thead>
          <tr>
            <th>归档编号</th><th>文件编号</th><th>策略版本</th><th>归档层级</th>
            <th>准入结果</th><th>归档状态</th><th>保留天数</th><th>到期日期</th>
            <th>分层依据</th><th>清理依据</th><th>冲突处理</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in archives" :key="keyOf(row, '归档编号')">
            <td>{{ row['归档编号'] }}</td>
            <td>{{ row['文件编号'] }}</td>
            <td>{{ row['策略版本'] }}</td>
            <td>{{ row['归档层级'] || '—' }}</td>
            <td>{{ row['准入结果'] }}</td>
            <td>{{ row['归档状态'] }}</td>
            <td>{{ row['保留天数'] }}</td>
            <td>{{ row['到期日期'] || '—' }}</td>
            <td>{{ row['分层依据'] || '—' }}</td>
            <td>{{ row['清理依据'] || '—' }}</td>
            <td>{{ row['冲突处理'] }}</td>
          </tr>
          <tr v-if="!archives.length">
            <td colspan="11" class="empty-state">暂无分层归档结果，可先执行分层归档</td>
          </tr>
        </tbody>
      </table>

      <h4>清理清单</h4>
      <table class="data-table" data-testid="storage-cleanup">
        <thead>
          <tr>
            <th>清理编号</th><th>文件编号</th><th>策略版本</th><th>归档层级</th>
            <th>清理状态</th><th>清理依据</th><th>冲突处理</th><th>延迟原因</th><th>新层级入口</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in cleanupRows" :key="keyOf(row, '清理编号')">
            <td>{{ row['清理编号'] }}</td>
            <td>{{ row['文件编号'] }}</td>
            <td>{{ row['策略版本'] }}</td>
            <td>{{ row['归档层级'] || '—' }}</td>
            <td>{{ row['清理状态'] }}</td>
            <td>{{ row['清理依据'] }}</td>
            <td>{{ row['冲突处理'] }}</td>
            <td>{{ row['延迟原因'] || '—' }}</td>
            <td>{{ row['新层级入口'] || '—' }}</td>
          </tr>
          <tr v-if="!cleanupRows.length">
            <td colspan="9" class="empty-state">暂无清理项</td>
          </tr>
        </tbody>
      </table>

      <h4>恢复清单</h4>
      <table class="data-table" data-testid="storage-recovery">
        <thead>
          <tr>
            <th>断点编号</th><th>文件编号</th><th>策略版本</th><th>原归档层级</th>
            <th>状态</th><th>新层级入口</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in recoveryRows" :key="`${keyOf(row, '断点编号')}-${keyOf(row, '文件编号')}`">
            <td>{{ row['断点编号'] }}</td>
            <td>{{ row['文件编号'] }}</td>
            <td>{{ row['策略版本'] }}</td>
            <td>{{ row['原归档层级'] || '—' }}</td>
            <td>{{ row['状态'] }}</td>
            <td>{{ row['新层级入口'] || '—' }}</td>
            <td>{{ row['说明'] }}</td>
          </tr>
          <tr v-if="!recoveryRows.length">
            <td colspan="7" class="empty-state">暂无恢复引用</td>
          </tr>
        </tbody>
      </table>

      <h4>历史回填迁移记录</h4>
      <table class="data-table" data-testid="storage-migrations">
        <thead>
          <tr>
            <th>迁移编号</th><th>归档编号</th><th>文件编号</th><th>策略版本</th>
            <th>回填层级</th><th>回填依据</th><th>回填状态</th><th>回填时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in migrations" :key="keyOf(row, '迁移编号')">
            <td>{{ row['迁移编号'] }}</td>
            <td>{{ row['归档编号'] }}</td>
            <td>{{ row['文件编号'] }}</td>
            <td>{{ row['策略版本'] }}</td>
            <td>{{ row['回填层级'] }}</td>
            <td>{{ row['回填依据'] }}</td>
            <td>{{ row['回填状态'] }}</td>
            <td>{{ row['回填时间'] }}</td>
          </tr>
          <tr v-if="!migrations.length">
            <td colspan="8" class="empty-state">暂无回填迁移记录</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Cell = string | number | boolean | null
type Row = Record<string, Cell>

function keyOf(row: Row, field: string): string {
  return String(row[field] ?? '')
}

const ENDPOINT = '/api/storage'
const columns = ["策略编号", "存储层级", "文件格式", "压缩算法", "保留天数", "迁移规则", "清理方式", "策略状态"]
const actions = ["启用策略", "暂停策略", "关闭策略"]
const statuses = ["草稿", "已生效", "已暂停", "已关闭"]
const stats = [{"label": "存储策略总数", "value": 0}, {"label": "已生效策略", "value": 0}, {"label": "已暂停策略", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const policyVersions = ref<string[]>([])
const archiveForm = ref({ policyVersion: '', precedence: '恢复终态优先' })
const archives = ref<Row[]>([])
const cleanupRows = ref<Row[]>([])
const recoveryRows = ref<Row[]>([])
const files = ref<Row[]>([])
const migrations = ref<Row[]>([])
const archiveStats = ref([
  { label: '归档结果', value: 0 },
  { label: '可用层级', value: 0 },
  { label: '恢复保留', value: 0 },
  { label: '已清理', value: 0 },
  { label: '清理延迟', value: 0 },
])
const actionMessage = ref('')

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '存储策略登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集存储分层动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集存储分层操作失败'
  }
}

async function postAction(path: string, body: Record<string, unknown>) {
  const response = await request(`${ENDPOINT}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.detail || payload.message || '操作未生效')
  }
  actionMessage.value = payload.message ?? '操作已完成'
  await loadArchiveView()
}

async function runArchive() {
  errorMessage.value = ''
  try {
    await postAction('/archive', {
      策略版本: archiveForm.value.policyVersion,
      终态优先级: archiveForm.value.precedence,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分层归档失败'
  }
}

async function runScan() {
  errorMessage.value = ''
  try {
    await postAction('/scan', { 策略版本: archiveForm.value.policyVersion })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '扫描存储文件失败'
  }
}

async function runRebuildCleanup() {
  errorMessage.value = ''
  try {
    await postAction('/cleanup/rebuild', {
      策略版本: archiveForm.value.policyVersion,
      终态优先级: archiveForm.value.precedence,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重建清理清单失败'
  }
}

async function runBackfill() {
  errorMessage.value = ''
  try {
    await postAction('/backfill', { 策略版本: archiveForm.value.policyVersion })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史回填失败'
  }
}

async function syncMerge() {
  errorMessage.value = ''
  try {
    await postAction('/merge-sync', { 策略版本: archiveForm.value.policyVersion })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同步聚合准入失败'
  }
}

async function loadArchiveView() {
  const overview = await fetchJson(`${ENDPOINT}/archive`)
  policyVersions.value = overview.policy_versions ?? []
  const selected = archiveForm.value.policyVersion
  const hasCurrent = (overview.items ?? []).some((item: Row) => item['策略版本'] === selected)
  if (!selected || !hasCurrent) {
    const versionsWithRows = new Set((overview.items ?? []).map((item: Row) => String(item['策略版本'] ?? '')))
    const fallback = [...policyVersions.value].reverse().find((version) => versionsWithRows.has(version))
    archiveForm.value.policyVersion = fallback ?? policyVersions.value[policyVersions.value.length - 1] ?? ''
  }
  const versionQuery = archiveForm.value.policyVersion
    ? `policy_version=${encodeURIComponent(archiveForm.value.policyVersion)}`
    : ''
  const [archivePayload, cleanupPayload, recoveryPayload, filePayload, migrationPayload] = await Promise.all([
    fetchJson(`${ENDPOINT}/archive?${versionQuery}`),
    fetchJson(`${ENDPOINT}/cleanup`),
    fetchJson(`${ENDPOINT}/recovery`),
    fetchJson(`${ENDPOINT}/files?${versionQuery}`),
    fetchJson(`${ENDPOINT}/migrations`),
  ])
  archives.value = archivePayload.items ?? []
  cleanupRows.value = cleanupPayload.items ?? []
  recoveryRows.value = recoveryPayload.items ?? []
  files.value = filePayload.items ?? []
  migrations.value = migrationPayload.items ?? []
  const archiveInfo = archivePayload.stats ?? {}
  archiveStats.value = [
    { label: '归档结果', value: archiveInfo.archive_total ?? archives.value.length },
    { label: '可用层级', value: archiveInfo.usable_total ?? 0 },
    { label: '恢复保留', value: archiveInfo.retained_total ?? 0 },
    { label: '已清理', value: archiveInfo.cleaned_total ?? 0 },
    { label: '清理延迟', value: archiveInfo.cleanup_deferred ?? 0 },
  ]
}

async function fetchJson(path: string) {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return response.json()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('存储策略列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集存储分层列表读取失败'
  }
}

async function bootstrap() {
  await reload()
  try {
    await loadArchiveView()
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '归档数据读取失败'
  }
}

onMounted(bootstrap)
</script>

<style scoped>
.archive-panel { margin-top: 24px; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; }
.policy-hint { font-size: 12px; color: var(--muted); }
.action-message { font-size: 13px; color: var(--brand); }
h4 { margin: 18px 0 8px; font-size: 14px; }
select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: #fff; }
</style>
