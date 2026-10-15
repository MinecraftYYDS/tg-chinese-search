<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

type Channel = { chat_id: number; title: string; username: string | null; collecting: number; published: number; status: string; error: string; processed: number; messages: number; history_done: number; last_sync: number | null; retry_at: number; history_before: number | null }
type Telegram = { phase: string; error: string; connected: boolean; expires_at?: number; qr_svg?: string; retry_at?: number; account: { id: number; name: string; username: string } | null }
type Settings = { telegram_api_id: string; api_hash_configured: boolean; proxy_enabled: boolean; proxy_configured: boolean; external_api_enabled: boolean; api_token_configured: boolean; bot_configured: boolean; bot_enabled: boolean; default_search_limit: number; max_random_limit: number }
type Item = { id: number; chat_id: number; message_id: number; text: string; source_link: string | null; channel_username: string | null; timestamp: number; hidden: boolean }

const ready = ref(false), initialized = ref(false), authenticated = ref(false), csrf = ref('')
const origin = window.location.origin
const confirm = window.confirm.bind(window)
const tab = ref('overview'), busy = ref(false), toast = ref(''), failure = ref(false)
const channels = ref<Channel[]>([]), messageCount = ref(0)
const telegram = ref<Telegram>({ phase: 'idle', error: '', connected: false, account: null })
const bot = ref({ running: false, username: '', error: '' })
const settings = ref<Settings | null>(null)
const auth = reactive({ username: 'admin', password: '', bootstrap_token: '' })
const config = reactive({ telegram_api_id: '', telegram_api_hash: '', proxy_enabled: false, proxy_url: '', external_api_enabled: false, bot_token: '', bot_enabled: false, default_search_limit: 50, max_random_limit: 10 })
const phone = ref(''), code = ref(''), twoFA = ref(''), loginMode = ref('qr')
const reference = ref(''), startDate = ref(''), joined = ref<{ chat_id: number; title: string; username: string }[]>([])
const search = reactive({ q: '', channel: '', show_hidden: false, since: '', until: '', offset: 0 })
const results = ref<Item[]>([]), total = ref(0), searched = ref(false)
const apiToken = ref(''), passwordForm = reactive({ username: 'admin', old_password: '', password: '' })
const audits = ref<{ id: number; action: string; detail: string; created_at: number }[]>([])
let timer: ReturnType<typeof setInterval> | undefined
let toastTimer: ReturnType<typeof setTimeout> | undefined
let refreshing = false

const nav = [
  { id: 'overview', icon: '◫', label: '概览', sub: 'OVERVIEW' },
  { id: 'channels', icon: '▤', label: '频道管理', sub: 'CHANNELS' },
  { id: 'search', icon: '⌕', label: '内容搜索', sub: 'LIBRARY' },
  { id: 'telegram', icon: '↗', label: 'Telegram 账号', sub: 'ACCOUNT' },
  { id: 'settings', icon: '⚙', label: '服务设置', sub: 'SETTINGS' },
  { id: 'audit', icon: '≡', label: '操作记录', sub: 'ACTIVITY' },
]
const current = computed(() => nav.find(n => n.id === tab.value)!)
const publishedCount = computed(() => channels.value.filter(c => c.published).length)
const qrImage = computed(() => telegram.value.qr_svg ? `data:image/svg+xml;base64,${btoa(telegram.value.qr_svg)}` : '')
const progress = computed(() => [
  { label: '配置应用凭据', done: !!settings.value?.api_hash_configured, target: 'settings' },
  { label: '登录采集账号', done: !!telegram.value.account, target: 'telegram' },
  { label: '添加指定频道', done: channels.value.length > 0, target: 'channels' },
])
const statusNames: Record<string, string> = { pending: '等待同步', syncing: '增量同步', backfilling: '回填历史', watching: '持续同步', paused: '已暂停', forbidden: '无访问权限', error: '同步异常', rate_limited: '限流等待', imported: '待验证' }
const date = (value: number | null | undefined) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未同步'

function notice(message: string, error = false) {
  toast.value = message; failure.value = error
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, error ? 10000 : 5000)
}

async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  const response = await fetch(`/api/admin${path}`, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(csrf.value ? { 'X-CSRF-Token': csrf.value } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') authenticated.value = false
    throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（HTTP ${response.status}）`)
  }
  return data
}

async function action(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  try { await fn() } catch (error) { notice(error instanceof Error ? error.message : '操作失败', true) }
  finally { busy.value = false }
}

async function refresh() {
  if (!authenticated.value || refreshing) return
  refreshing = true
  try {
    const data = await api('/overview')
    channels.value = data.channels; telegram.value = data.telegram; bot.value = data.bot
    messageCount.value = data.messages; settings.value = data.settings
  } finally { refreshing = false }
}

function fillConfig() {
  if (!settings.value) return
  Object.assign(config, {
    telegram_api_id: settings.value.telegram_api_id, proxy_enabled: settings.value.proxy_enabled,
    external_api_enabled: settings.value.external_api_enabled, bot_enabled: settings.value.bot_enabled,
    default_search_limit: settings.value.default_search_limit, max_random_limit: settings.value.max_random_limit,
    telegram_api_hash: '', proxy_url: '', bot_token: '',
  })
}

async function navigate(id: string) {
  apiToken.value = ''; tab.value = id
  if (id === 'settings') fillConfig()
  if (id === 'audit') await action(async () => { audits.value = await api('/audit') })
  if (id === 'search' && !searched.value) await action(fetchSearch)
}

async function signIn() {
  await action(async () => {
    const data = await api(initialized.value ? '/login' : '/setup', 'POST', initialized.value ? { username: auth.username, password: auth.password } : auth)
    csrf.value = data.csrf; authenticated.value = true; initialized.value = true
    auth.password = ''; auth.bootstrap_token = ''
    await refresh(); fillConfig()
  })
}

async function logout() {
  await action(async () => { await api('/logout', 'POST'); authenticated.value = false; csrf.value = ''; apiToken.value = '' })
}

async function saveSettings() {
  await action(async () => {
    const payload: Record<string, unknown> = {
      proxy_enabled: config.proxy_enabled, external_api_enabled: config.external_api_enabled,
      bot_enabled: config.bot_enabled, default_search_limit: config.default_search_limit, max_random_limit: config.max_random_limit,
    }
    if (config.telegram_api_id) payload.telegram_api_id = Number(config.telegram_api_id)
    if (config.telegram_api_hash) payload.telegram_api_hash = config.telegram_api_hash.trim()
    if (config.proxy_url) payload.proxy_url = config.proxy_url.trim()
    if (config.bot_token) payload.bot_token = config.bot_token.trim()
    settings.value = await api('/settings', 'PATCH', payload)
    fillConfig(); notice('设置已保存'); await refresh()
  })
}

async function testConnection() {
  await action(async () => { notice((await api('/telegram/test', 'POST')).message) })
}

async function telegramAction(path: string, body?: unknown) {
  await action(async () => {
    telegram.value = await api(`/telegram/${path}`, 'POST', body)
    code.value = ''; twoFA.value = ''
    if (path === 'phone') phone.value = ''
    await refresh()
  })
  twoFA.value = ''; code.value = ''
}

async function addChannel(refValue = reference.value) {
  await action(async () => {
    await api('/channels', 'POST', { reference: refValue, start_time: startDate.value ? Math.floor(new Date(`${startDate.value}T00:00:00`).getTime() / 1000) : 0 })
    reference.value = ''; notice('频道已添加，开始同步；默认不对外提供内容'); await refresh()
  })
}

async function channelAction(channel: Channel, body: object) {
  if ('published' in body && (body as any).published && !confirm(`确认将「${channel.title}」提供给 TG 搜索机器人和外部 API？其中的内容将可能被 QQ 用户获取。`)) return
  await action(async () => { await api(`/channels/${channel.chat_id}`, 'PATCH', body); await refresh() })
}

async function removeChannel(channel: Channel) {
  if (!confirm(`移除「${channel.title}」的采集与对外查询配置？已有数据默认保留，管理员仍可搜索。`)) return
  const deleteData = confirm('是否同时永久删除该频道已有消息？\n确定：永久删除数据\n取消：保留数据，仅移除配置')
  await action(async () => { await api(`/channels/${channel.chat_id}?delete_data=${deleteData}`, 'DELETE'); notice('频道已移除'); await refresh() })
}

async function fetchSearch() {
  const params = new URLSearchParams({ q: search.q, offset: String(search.offset), limit: '20', show_hidden: String(search.show_hidden) })
  if (search.channel) params.set('channel', search.channel)
  if (search.since) params.set('since', String(Math.floor(new Date(`${search.since}T00:00:00`).getTime() / 1000)))
  if (search.until) params.set('until', String(Math.floor(new Date(`${search.until}T23:59:59`).getTime() / 1000)))
  const data = await api(`/search?${params}`)
  results.value = data.items; total.value = data.total; searched.value = true
}

async function find() { search.offset = 0; await action(fetchSearch) }
async function page(delta: number) { search.offset += delta; await action(fetchSearch) }
async function hide(item: Item) {
  await action(async () => { await api(`/messages/${item.chat_id}/${item.message_id}`, 'PATCH', { hidden: !item.hidden }); await fetchSearch() })
}
async function rotateToken() {
  if (!confirm('生成新 Token 后，旧 Token 立即失效，需要同步更新 bot 配置。继续？')) return
  await action(async () => { apiToken.value = (await api('/api-token', 'POST')).token; notice('新 Token 仅显示这一次，请立即保存') })
}
async function copyToken() {
  try { await navigator.clipboard.writeText(apiToken.value); notice('已复制') } catch { notice('无法自动复制，请手动选中复制', true) }
}
async function changePassword() {
  await action(async () => {
    await api('/password', 'POST', passwordForm)
    passwordForm.password = ''; passwordForm.old_password = ''; authenticated.value = false; csrf.value = ''
    notice('密码已修改，所有会话已退出，请重新登录')
  })
}

onMounted(async () => {
  try {
    const data = await api('/session')
    initialized.value = data.initialized; authenticated.value = data.authenticated; csrf.value = data.csrf || ''
    if (authenticated.value) { await refresh(); fillConfig() }
  } catch (error) { notice(error instanceof Error ? error.message : '无法连接服务器', true) }
  finally { ready.value = true }
  timer = setInterval(() => { if (!busy.value && !document.hidden) refresh().catch(() => {}) }, 5000)
})
onUnmounted(() => { clearInterval(timer); clearTimeout(toastTimer) })
</script>

<template>
  <div v-if="!ready" class="loading">正在连接频道书签…</div>
  <main v-else-if="!authenticated" class="auth-shell">
    <section class="auth-story">
      <div class="brand"><span class="brand-mark">↗</span><span>频道书签<small>TELEGRAM LIBRARY</small></span></div>
      <span class="eyebrow">YOUR CHANNELS. YOUR LIBRARY.</span>
      <h1>让好内容，<br>不再沉入历史。</h1>
      <p>只收藏你指定的频道。自动整理历史，持续同步更新，让每一句值得留下的话都能被找到。</p>
      <div class="story-tags"><span>中文全文搜索</span><span>自动历史回填</span><span>私有化部署</span></div>
      <small class="story-footer">一个账号 · 指定频道 · 安全可控</small>
    </section>
    <section class="auth-form panel">
      <span class="eyebrow">{{ initialized ? 'WELCOME BACK' : 'FIRST TIME SETUP' }}</span>
      <h2>{{ initialized ? '登录管理后台' : '创建你的内容工作台' }}</h2>
      <p class="muted">{{ initialized ? '使用管理员账号继续，不是 Telegram 手机号。' : '仅需初始化一次，之后所有配置都可以在这里完成。' }}</p>
      <form @submit.prevent="signIn">
        <label v-if="!initialized">一次性初始化凭据<input v-model="auth.bootstrap_token" type="password" required autocomplete="off" placeholder="服务数据目录中的 bootstrap-token.txt" /></label>
        <label>管理员用户名<input v-model="auth.username" required autocomplete="username" pattern="[a-zA-Z0-9_.-]+" maxlength="64" /></label>
        <label>管理员密码<input v-model="auth.password" type="password" required :minlength="initialized ? 1 : 12" :autocomplete="initialized ? 'current-password' : 'new-password'" placeholder="至少 12 位，建议使用密码管理器" /></label>
        <button class="primary wide" :disabled="busy">{{ busy ? '正在处理…' : initialized ? '登录工作台 →' : '创建管理员 →' }}</button>
      </form>
      <p class="fine">Telegram 登录在进入后台后完成。验证码和两步验证密码不会持久化保存。</p>
    </section>
  </main>

  <div v-else class="workspace">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">↗</span><span>频道书签<small>TELEGRAM LIBRARY</small></span></div>
      <span class="nav-caption">工作空间</span>
      <nav><button v-for="item in nav" :key="item.id" :class="{ active: tab === item.id }" @click="navigate(item.id)"><span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}<small>{{ item.sub }}</small></span></button></nav>
      <div class="sidebar-bottom"><div class="connection"><i :class="{ online: telegram.account && telegram.connected }"></i>{{ telegram.account ? telegram.connected ? '采集账号已连接' : '采集账号重连中' : '采集账号未登录' }}</div><button class="quiet" @click="logout" :disabled="busy">退出管理后台 ↗</button><small>私有内容默认不对外共享</small></div>
    </aside>

    <main class="content">
      <header class="page-header"><div><span class="eyebrow">{{ current.sub }}</span><h1>{{ current.label }}</h1></div><div class="header-actions"><button class="secondary" @click="action(refresh)" :disabled="busy">↻ 刷新状态</button><button class="quiet" @click="logout" :disabled="busy">退出</button></div></header>

      <template v-if="tab === 'overview'">
        <section class="welcome panel"><div><span class="pill">你的专属频道资料库</span><h2>从一条消息，到可检索的收藏。</h2><p>历史自动回填，更新持续同步。你决定采集什么，也决定分享什么。</p></div><span class="welcome-art">↗</span></section>
        <div class="stats"><article><small>已配置频道</small><strong>{{ channels.length }}</strong><span>仅采集明确指定的频道</span></article><article><small>已存储消息</small><strong>{{ messageCount.toLocaleString() }}</strong><span>文字与媒体说明文字</span></article><article><small>对外开放频道</small><strong>{{ publishedCount }}</strong><span>TG 搜索机器人与 API 共用</span></article><article><small>采集账号</small><strong class="status-word">{{ telegram.account ? telegram.connected ? '已连接' : '重连中' : '待登录' }}</strong><span>{{ telegram.account?.name || '在 WebUI 内安全登录' }}</span></article></div>
        <section v-if="progress.some(p => !p.done)" class="panel"><div class="section-head"><h2>开始使用</h2><span class="muted">{{ progress.filter(p => p.done).length }} / 3 已完成</span></div><div class="setup-steps"><button v-for="(step, index) in progress" :key="step.target" @click="navigate(step.target)"><span :class="['step-number', { done: step.done }]">{{ step.done ? '✓' : index + 1 }}</span><span>{{ step.label }}</span><span>→</span></button></div></section>
        <div v-if="telegram.error" class="alert">{{ telegram.error }}<span v-if="telegram.retry_at"> · 等待至 {{ date(telegram.retry_at) }}</span></div>
        <section class="panel"><div class="section-head"><h2>同步动态</h2><button class="text-button" @click="navigate('channels')">管理频道 →</button></div><div v-if="!channels.length" class="empty"><span>▤</span><h3>还没有添加频道</h3><p>登录 Telegram 后，添加第一个指定频道。</p><button class="primary" @click="navigate(telegram.account ? 'channels' : 'telegram')">{{ telegram.account ? '添加频道' : '登录 Telegram' }}</button></div><div v-for="channel in channels.slice(0, 6)" :key="channel.chat_id" class="activity-row"><span class="channel-avatar">{{ channel.title.slice(0, 1) }}</span><div><strong>{{ channel.title }}</strong><small>{{ channel.messages.toLocaleString() }} 条消息 · {{ date(channel.last_sync) }}</small></div><span :class="['badge', { warn: !!channel.error }]">{{ statusNames[channel.status] || channel.status }}</span></div></section>
      </template>

      <template v-if="tab === 'telegram'">
        <div class="section-intro"><h2>让采集账号替你整理频道</h2><p>这是后台采集用的 Telegram 用户账号，与管理员登录、TG 搜索机器人相互独立。</p></div>
        <div v-if="!settings?.api_hash_configured" class="alert">请先配置 API ID / API Hash。<button class="text-button" @click="navigate('settings')">前往设置 →</button></div>
        <div v-if="telegram.error" class="alert">{{ telegram.error }}</div>
        <section v-if="telegram.account" class="panel account-card"><span class="big-avatar">{{ telegram.account.name.slice(0, 1) }}</span><h2>{{ telegram.account.name }}</h2><p>@{{ telegram.account.username || '无公开用户名' }} · {{ telegram.account.id }}</p><span class="badge">{{ telegram.connected ? '连接正常' : '正在重新连接' }}</span><p class="muted">授权已加密保存。服务重启后自动恢复，只采集你指定的频道。</p><button class="danger" :disabled="busy" @click="confirm('退出将撤销当前采集授权并暂停同步，但保留历史数据。继续？') && telegramAction('logout')">退出采集账号</button></section>
        <section v-else class="panel login-panel">
          <template v-if="telegram.phase === 'password'"><h2>输入两步验证密码</h2><p class="muted">你的 Telegram 账号开启了两步验证。密码仅用于本次登录，不会保存。</p><form @submit.prevent="telegramAction('password', { password: twoFA })"><label>两步验证密码<input v-model="twoFA" type="password" required autocomplete="off" /></label><button class="primary" :disabled="busy">确认登录</button></form></template>
          <template v-else-if="telegram.phase === 'code'"><h2>查看 Telegram 中的验证码</h2><p class="muted">验证码可能发送到 Telegram 客户端，是否提供短信由 Telegram 决定。</p><form @submit.prevent="telegramAction('code', { code })"><label>登录验证码<input v-model="code" inputmode="numeric" pattern="\d{4,8}" required autocomplete="one-time-code" /></label><button class="primary" :disabled="busy">验证并继续</button></form></template>
          <template v-else><div class="segmented"><button :class="{ selected: loginMode === 'qr' }" @click="loginMode = 'qr'">扫码登录</button><button :class="{ selected: loginMode === 'phone' }" @click="loginMode = 'phone'">手机号登录</button></div><div v-if="loginMode === 'qr'" class="qr-panel"><img v-if="qrImage" :src="qrImage" alt="Telegram 登录二维码" /><div v-else class="qr-placeholder">↗</div><h3>在手机 Telegram 中确认登录</h3><p>设置 → 设备 → 关联桌面设备<br />请勿将二维码分享给他人</p><p v-if="telegram.expires_at" class="fine">有效期至 {{ date(telegram.expires_at) }}</p><button v-if="telegram.phase !== 'qr'" class="primary" :disabled="busy || !settings?.api_hash_configured" @click="telegramAction('qr')">生成登录二维码</button><span v-else class="badge">等待手机确认…</span></div><form v-else @submit.prevent="telegramAction('phone', { phone })"><h2>使用手机号登录</h2><p class="muted">包括国家区号，例如 +8613812345678。</p><label>Telegram 手机号<input v-model="phone" type="tel" required pattern="\+[1-9]\d{6,14}" placeholder="+86…" autocomplete="off" /></label><button class="primary" :disabled="busy || !settings?.api_hash_configured || telegram.phase === 'qr'">发送验证码</button></form></template>
          <button v-if="telegram.phase !== 'idle'" class="quiet cancel-login" :disabled="busy" @click="telegramAction('cancel')">取消当前登录流程</button>
        </section>
        <p class="fine">登录凭据只能通过 HTTPS 或本机连接提交。请使用你有权访问、采集目标频道的账号。</p>
      </template>

      <template v-if="tab === 'channels'">
        <section class="panel"><div class="section-head"><h2>添加指定频道</h2><span class="pill">默认仅管理员可见</span></div><form class="add-form" @submit.prevent="addChannel()"><label class="grow">频道用户名或链接<input v-model="reference" placeholder="@channel / https://t.me/channel" required maxlength="300" /></label><label>历史起始日期（可选）<input v-model="startDate" type="date" /></label><button class="primary" :disabled="busy || !telegram.account">添加并同步 →</button></form><div class="inline-note"><span>私有频道需要采集账号先加入，不会自动接受邀请或加入其他频道。</span><button class="text-button" :disabled="busy || !telegram.account" @click="action(async () => { joined = await api('/telegram/channels') })">选择已加入的频道</button></div><div v-if="joined.length" class="joined-list"><button v-for="channel in joined" :key="channel.chat_id" :disabled="busy" @click="addChannel(String(channel.chat_id))">＋ {{ channel.title }} <small>{{ channel.username ? '@' + channel.username : '私有频道' }}</small></button></div></section>
        <div class="section-head"><h2>频道列表 <span class="muted">{{ channels.length }}</span></h2><span class="muted">历史回填可断点续传</span></div>
        <div v-if="!channels.length" class="panel empty"><span>▤</span><h3>只采集你选择的内容</h3><p>添加频道后自动获取可见历史，不扫描其他聊天。</p></div>
        <section v-for="channel in channels" :key="channel.chat_id" class="panel channel-card"><div class="channel-heading"><span class="channel-avatar">{{ channel.title.slice(0, 1) }}</span><div class="grow"><h3>{{ channel.title }}</h3><small>{{ channel.username ? '@' + channel.username : '私有频道' }} · {{ channel.chat_id }}</small></div><span :class="['badge', { warn: !!channel.error }]">{{ statusNames[channel.status] || channel.status }}</span></div><div class="channel-metrics"><div><small>已存储消息</small><strong>{{ channel.messages.toLocaleString() }}</strong></div><div><small>已处理历史</small><strong>{{ channel.processed.toLocaleString() }} <span>{{ channel.history_done ? '· 已完成' : '· 回填中' }}</span></strong></div><div><small>最近同步</small><strong class="small-value">{{ date(channel.last_sync) }}</strong></div></div><p v-if="channel.error" class="alert">{{ channel.error }}<span v-if="channel.retry_at"> · 重试时间 {{ date(channel.retry_at) }}</span></p><div class="channel-actions"><button :class="channel.published ? 'published' : 'secondary'" :disabled="busy" @click="channelAction(channel, { published: !channel.published })">{{ channel.published ? '✓ 已允许对外查询' : '○ 仅管理员可见' }}</button><div><button class="secondary" :disabled="busy" @click="channelAction(channel, { collecting: !channel.collecting })">{{ channel.collecting ? '暂停采集' : '恢复采集' }}</button><button class="quiet" :disabled="busy" @click="channelAction(channel, { retry: true })">重试</button><button class="danger quiet" :disabled="busy" @click="removeChannel(channel)">移除</button></div></div></section>
      </template>

      <template v-if="tab === 'search'">
        <section class="panel"><form @submit.prevent="find"><div class="search-line"><input v-model="search.q" placeholder="输入中文关键词，留空浏览内容…" maxlength="1000" aria-label="搜索关键词" /><button class="primary" :disabled="busy">搜索 →</button></div><div class="filters"><label>频道<select v-model="search.channel"><option value="">全部已存储内容</option><option v-for="channel in channels" :key="channel.chat_id" :value="String(channel.chat_id)">{{ channel.title }}</option></select></label><label>起始日期<input v-model="search.since" type="date" /></label><label>截止日期<input v-model="search.until" type="date" /></label><label class="checkbox"><input v-model="search.show_hidden" type="checkbox" />包含已屏蔽内容</label></div></form></section>
        <div class="section-head"><h2>搜索结果</h2><span class="muted">共 {{ total.toLocaleString() }} 条 · 管理员可查看未公开频道</span></div><div v-if="!results.length" class="panel empty"><span>⌕</span><h3>暂时没有找到内容</h3><p>试试其他关键词，或等待频道同步。</p></div><article v-for="item in results" :key="item.id" :class="['panel result-card', { hidden: item.hidden }]"><div class="result-meta"><span>{{ item.channel_username ? '@' + item.channel_username : item.chat_id }}</span><time>{{ date(item.timestamp) }}</time><span v-if="item.hidden" class="badge warn">已屏蔽</span></div><p class="message-text">{{ item.text }}</p><div class="result-footer"><small>{{ item.chat_id }}:{{ item.message_id }}</small><div><a v-if="item.source_link?.startsWith('https://t.me/')" :href="item.source_link" target="_blank" rel="noopener noreferrer">查看原文 ↗</a><button class="text-button" :disabled="busy" @click="hide(item)">{{ item.hidden ? '取消屏蔽' : '屏蔽内容' }}</button></div></div></article><div v-if="total > 20" class="pagination"><button class="secondary" :disabled="busy || search.offset === 0" @click="page(-20)">← 上一页</button><span>{{ Math.floor(search.offset / 20) + 1 }} / {{ Math.ceil(total / 20) }}</span><button class="secondary" :disabled="busy || search.offset + 20 >= total" @click="page(20)">下一页 →</button></div>
      </template>

      <template v-if="tab === 'settings'">
        <form @submit.prevent="saveSettings">
          <section class="panel"><div class="section-head"><h2>Telegram 应用凭据</h2><a href="https://my.telegram.org/apps" target="_blank" rel="noopener noreferrer">申请 API ID / Hash ↗</a></div><p class="muted">应用凭据不是 Bot Token；历史采集必须登录用户账号。Hash 加密保存，不提供明文回读。</p><div class="form-grid"><label>API ID<input v-model="config.telegram_api_id" inputmode="numeric" placeholder="数字应用 ID" /></label><label>API Hash<input v-model="config.telegram_api_hash" type="password" autocomplete="off" :placeholder="settings?.api_hash_configured ? '已配置，留空保留原值' : '32 位应用 Hash'" /></label></div></section>
          <section class="panel"><h2>网络代理</h2><label class="checkbox"><input v-model="config.proxy_enabled" type="checkbox" />启用 Telegram 代理</label><label>代理地址<input v-model="config.proxy_url" type="password" autocomplete="off" :placeholder="settings?.proxy_configured ? '已配置，留空保留原值' : 'socks5://user:password@host:port'" /></label><p class="fine">支持 SOCKS5、SOCKS4、HTTP，仅用于 Telegram 连接。代理失败不会自动绕过直连。先保存设置，再测试连接。</p><button type="button" class="secondary" :disabled="busy || !settings?.api_hash_configured" @click="testConnection">测试 Telegram 连接</button></section>
          <section class="panel"><div class="section-head"><h2>对外查询 API</h2><span class="pill">兼容 bot #wa</span></div><label class="checkbox"><input v-model="config.external_api_enabled" type="checkbox" />启用搜索 / 随机接口</label><div class="code-block">GET {{ origin }}/api/search?q=你好<br />GET {{ origin }}/api/random?limit=1<br />Authorization: Bearer &lt;你的 API Token&gt;</div><p class="fine">只返回开启「对外查询」的频道内容，所有请求必须使用 Bearer Token。</p><div class="form-grid"><label>默认搜索条数<input v-model.number="config.default_search_limit" type="number" min="1" max="200" /></label><label>随机条数上限<input v-model.number="config.max_random_limit" type="number" min="1" max="200" /></label></div><button type="button" class="secondary" :disabled="busy" @click="rotateToken">生成 / 重置 API Token</button><div v-if="apiToken" class="token-box"><strong>仅显示这一次，请保存：</strong><code>{{ apiToken }}</code><button type="button" class="secondary" @click="copyToken">复制 Token</button></div><p class="fine">bot 配置仍使用 EXTERNAL_API_HOST / PORT / TOKEN；跨服务器调用 HTTPS 的配置方式见部署文档。</p></section>
          <section class="panel"><h2>TG 搜索机器人</h2><p class="muted">可选。负责私聊、Inline 和 /sj 交互，不承担频道采集。</p><label>Bot Token<input v-model="config.bot_token" type="password" autocomplete="off" :placeholder="settings?.bot_configured ? '已配置，留空保留原值' : '从 @BotFather 获取'" /></label><label class="checkbox"><input v-model="config.bot_enabled" type="checkbox" />启用 TG 搜索机器人（polling）</label><p class="fine">Inline 模式需在 BotFather 开启。若旧服务使用 webhook，请先停用旧部署并清除 webhook。</p><div v-if="bot.error" class="alert">{{ bot.error }}</div><span class="badge">{{ bot.running ? '@' + bot.username + ' 运行中' : '未运行' }}</span></section>
          <div class="save-bar"><span>敏感配置留空保留原值</span><button class="primary" :disabled="busy">{{ busy ? '正在保存…' : '保存服务设置' }}</button></div>
        </form>
        <section class="panel"><h2>管理员账号</h2><p class="muted">修改后所有管理会话立即失效，需要重新登录。</p><form @submit.prevent="changePassword"><div class="form-grid"><label>用户名<input v-model="passwordForm.username" required autocomplete="username" pattern="[a-zA-Z0-9_.-]+" /></label><label>原密码<input v-model="passwordForm.old_password" type="password" required autocomplete="current-password" /></label><label>新密码<input v-model="passwordForm.password" type="password" required minlength="12" autocomplete="new-password" /></label></div><button class="secondary" :disabled="busy">修改管理员账号</button></form></section>
      </template>

      <template v-if="tab === 'audit'"><div class="section-intro"><h2>每一次管理，都有迹可循</h2><p>显示最近 100 次操作，不记录验证码、密码、session 或 Token。</p></div><section class="panel"><div v-if="!audits.length" class="empty">暂无操作记录</div><div v-for="record in audits" :key="record.id" class="audit-row"><time>{{ date(record.created_at) }}</time><strong>{{ record.action }}</strong><span>{{ record.detail || '—' }}</span></div></section></template>
      <footer class="page-footer">频道书签 · Telegram 中文搜索 <span>只采集你指定的频道</span></footer>
    </main>
  </div>
  <div v-if="toast" role="status" :class="['toast', { failure }]">{{ failure ? '⚠' : '✓' }} {{ toast }}<button @click="toast = ''" aria-label="关闭提示">×</button></div>
  <div v-if="busy" class="busy-line"></div>
</template>
