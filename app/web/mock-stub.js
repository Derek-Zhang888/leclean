/* 预览模式用的 API 桩：仅当以 index.html?preview 打开时加载。
   真实运行时完全不会加载本文件。 */
(function () {
  if (!window.__MOCK_MODE__ || !window.__MOCK__) return;
  const D = window.__MOCK__;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  function evt(event, payload) {
    if (typeof window.__evt === 'function') window.__evt({ event, payload: payload || {}, ts: Date.now() / 1000 });
  }

  const DEMO_LOCKERS = [
    { pid: 4820, app: 'explorer.exe', exe: 'C:\\Windows\\explorer.exe', exe_name: 'explorer.exe',
      service: '', type: '资源管理器', type_code: 4, restartable: true, is_service: false,
      protected: false, can_kill: true },
    { pid: 8821, app: 'QQ.exe', exe: 'D:\\QQNT\\QQ.exe', exe_name: 'QQ.exe',
      service: '', type: '主窗口应用', type_code: 1, restartable: false, is_service: false,
      protected: false, can_kill: true },
    { pid: 912, app: 'lsass.exe', exe: 'C:\\Windows\\System32\\lsass.exe', exe_name: 'lsass.exe',
      service: '', type: '关键系统进程', type_code: 1000, restartable: false, is_service: false,
      protected: true, can_kill: false },
  ];
  const demoLock = (path, cleared) => ({
    ok: true, available: true, error: '', paths: [path],
    lockers: cleared ? [] : DEMO_LOCKERS,
  });

  window.pywebview = {
    api: {
      async get_meta() { return D.meta; },
      async set_config() { return { ok: true }; },
      async locate_wiztree() { return { ok: true, path: D.meta.wiztree }; },
      async list_snapshots() { return { ok: true, items: [] }; },
      async get_log() { return { ok: true, log: ['[预览模式] 这是模拟数据'] }; },

      async start_scan() {
        (async () => {
          const steps = [
            ['引擎就绪：WizTree64.exe', 3],
            ['读取注册表（已安装程序 / App Paths）…', 6],
            [`注册表：识别到 ${D.result.registry.total} 个已安装程序`, 12],
            ['扫描 C: 盘（WizTree 导出中）…', 20],
            ['解析 C: 盘数据…', 34],
            ['C: 盘完成', 42],
            ['扫描 D: 盘（WizTree 导出中）…', 55],
            ['解析 D: 盘数据…', 68],
            ['D: 盘完成', 72],
            ['来源判定与风险分级…', 82],
            ['生成快照并与上次对比…', 92],
            ['完成', 100],
          ];
          for (const [msg, pct] of steps) { evt('progress', { msg, pct }); await sleep(220); }
          evt('scan_result', D.result);
          evt('done', { msg: '扫描结束' });
        })();
        return { ok: true, started: true };
      },

      async get_result() { return D.result; },
      async get_diff() { return D.result.diff; },

      async get_cache_report() {
        (async () => {
          evt('progress', { msg: '缓存/临时目录专项扫描中…' });
          await sleep(700);
          evt('cache_result', { ok: true, items: D.cache });
        })();
        return { ok: true, started: true };
      },

      async get_software() {
        (async () => {
          evt('progress', { msg: '统计已安装软件占用…' });
          await sleep(700);
          evt('software_result', { ok: true, apps: D.software, orphans: D.orphans });
        })();
        return { ok: true, started: true };
      },

      async move_to_recycle_bin(paths) {
        await sleep(1200);
        const list = (paths || []).map(p => p.path || p);
        return { ok: list, ok_count: list.length, failed: [], failed_count: 0,
                 cancelled: false, total: list.length, remaining: 0, aborted: false };
      },

      async hide_to_tray() { return { ok: true }; },
      async restore_window() { return { ok: true }; },
      async setup_tray() { return { available: true, error: '' }; },
      async tray_status() {
        return { available: true, running: false, error: '', close_action: 'ask' };
      },
      async set_close_action(a) { return { ok: true, close_action: a }; },
      async get_close_action() { return { ok: true, close_action: 'ask' }; },
      async close_with(action, remember) {
        return { ok: true, action, remembered: !!remember,
                 message: action === 'tray' ? '已最小化到系统托盘（预览模式）'
                                            : '正在退出（预览模式）' };
      },
      async get_about() {
        return {
          app: '乐清理', version: '1.0',
          author: {
            name: 'Derek888', login: 'Derek-Zhang888', bio: 'vibe coding',
            url: 'https://github.com/Derek-Zhang888',
            avatar: 'https://avatars.githubusercontent.com/u/143163018?v=4',
            joined: '2023-08-25', repos: 1,
          },
          repo_url: '',
          base_dir: 'D:\\App', data_dir: 'D:\\App\\data',
          python: '3.13.12', frozen: true, is_admin: false, wiztree: 'WizTree64.exe',
        };
      },

      async refresh_volumes() {
        return { ok: true, volumes: D.meta.volumes, physical: D.meta.physical };
      },

      async check_locks(path) {
        (async () => {
          evt('progress', { msg: '正在分析占用：' + path });
          await sleep(650);
          evt('lock_result', demoLock(path));
        })();
        return { ok: true };
      },

      async unlock(path, action) {
        await sleep(550);
        const msg = {
          delay: '已登记：下次重启时系统会自动删除（预览模式，未真的执行）',
          kill: '已结束进程（预览模式，未真的执行）',
          restart: '已结束并重新启动（预览模式，未真的执行）',
          restart_explorer: '已重启资源管理器（预览模式，未真的执行）',
        }[action] || '已执行';
        const r = { ok: true, action, path, message: msg, diagnosis: demoLock(path, true) };
        evt('unlock_result', r);
        return r;
      },
      async explorer_restart() { return { ok: true, message: '（预览模式）' }; },

      async test_sound(name) {
        // 预览模式：用 Web Audio 现合成一个短促“嘀”声代替
        try {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          const ctx = new Ctx();
          const osc = ctx.createOscillator(), g = ctx.createGain();
          const freq = { start_scan: 587, end_scan: 880, start_clean: 466,
                         end_clean: 1175, warn: 622, error: 349 }[name] || 700;
          osc.frequency.value = freq;
          osc.type = 'sine';
          g.gain.value = 0.08;
          osc.connect(g); g.connect(ctx.destination);
          osc.start();
          g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
          osc.stop(ctx.currentTime + 0.4);
          setTimeout(() => ctx.close(), 800);
        } catch (e) { /* 忽略 */ }
        return { ok: true, name, available: true };
      },
      async request_close() { return { allow: true }; },
      async cancel_current() { return { ok: true, task: '扫描' }; },
      async force_close() { return { ok: true }; },
      async open_path() { return { ok: true }; },
      async reveal_path() { return { ok: true }; },
      async run_uninstaller() { return { ok: true }; },
      async export_report() { return { ok: true, path: '（预览模式不写文件）' }; },
      async get_whitelist() { return { ok: true, items: D.meta.whitelist }; },
      async add_whitelist(p) { D.meta.whitelist.push(p); return { ok: true, items: D.meta.whitelist }; },
      async remove_whitelist(p) {
        D.meta.whitelist = D.meta.whitelist.filter(x => x !== p);
        return { ok: true, items: D.meta.whitelist };
      },
    },
  };
})();
