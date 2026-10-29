export interface BundledToolFixture {
  pluginId: string;
  toolName: string;
  rendererId: string;
  title: string;
  data: Record<string, unknown>;
}

const localImage =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='96'%3E%3Crect width='160' height='96' rx='12' fill='%23dbeafe'/%3E%3Ccircle cx='48' cy='40' r='20' fill='%2360a5fa'/%3E%3Cpath d='M10 90 60 48l30 24 18-15 42 33' fill='%2334d399'/%3E%3C/svg%3E";
const futureCountdown = new Date(Date.now() + 60 * 60_000).toISOString();

export const bundledToolFixtures: BundledToolFixture[] = [
  {
    pluginId: "air-quality",
    toolName: "get_air_quality",
    rendererId: "air_quality_card",
    title: "空气质量 · 本地夹具",
    data: {
      city: "杭州",
      latitude: 30.27,
      longitude: 120.15,
      pm2_5: 18,
      pm10: 31,
      us_aqi: 42,
      european_aqi: 16,
      aqi_label: "Good",
    },
  },
  {
    pluginId: "bilibili-anime",
    toolName: "bilibili_search_anime",
    rendererId: "bilibili_player",
    title: "番剧搜索 · 不提供外部播放",
    data: {
      keyword: "春季番剧",
      results: [
        {
          season_id: 10001,
          title: "本地播放边界样例",
          episodes: [
            {
              index: 1,
              label: "第 1 集",
              bvid: "",
              aid: null,
              cid: null,
              badge: "可选中但不可外播",
            },
            {
              index: 2,
              label: "第 2 集",
              bvid: "",
              aid: null,
              cid: null,
              badge: "可选中但不可外播",
            },
          ],
        },
        {
          season_id: 10002,
          title: "另一个本地番剧样例",
          episodes: [
            {
              index: 1,
              label: "第 1 集",
              bvid: "",
              aid: null,
              cid: null,
              badge: "可选中但不可外播",
            },
          ],
        },
      ],
      selected_season_id: 10001,
      current_episode: null,
    },
  },
  {
    pluginId: "color-convert",
    toolName: "convert_color",
    rendererId: "color_convert_card",
    title: "色彩转换",
    data: {
      input: "#3b82f6",
      hex: "#3b82f6",
      rgb: { r: 59, g: 130, b: 246 },
      hsl: { h: 217, s: 91, l: 60 },
    },
  },
  {
    pluginId: "daily-english",
    toolName: "daily_english",
    rendererId: "daily_english_card",
    title: "每日英语",
    data: {
      word: "fixture",
      phonetic: "/ˈfɪkstʃər/",
      meaning_zh: "本地演示夹具",
      example: "This card uses a local fixture.",
      daily: true,
      date: "2026-10-26",
    },
  },
  {
    pluginId: "fortune",
    toolName: "roll_dice",
    rendererId: "fortune_card",
    title: "掷骰子分支",
    data: { kind: "dice", notation: "2d6", rolls: [3, 5], total: 8 },
  },
  {
    pluginId: "fortune",
    toolName: "daily_fortune",
    rendererId: "fortune_card",
    title: "运势分支",
    data: {
      kind: "fortune",
      name: "平稳前行",
      score: 78,
      summary: "脚本化的运势卡片。",
      lucky_color: "蓝色",
      do: "整理计划",
      dont: "仓促决定",
      date: "2026-10-26",
    },
  },
  {
    pluginId: "fortune",
    toolName: "draw_lot",
    rendererId: "fortune_card",
    title: "抽签分支",
    data: { kind: "lot", title: "中签", verse: "一步一景，循序而行。" },
  },
  {
    pluginId: "fun-facts",
    toolName: "random_fun_fact",
    rendererId: "fun_facts_card",
    title: "趣味知识分支",
    data: { kind: "fact", content: "章鱼有三颗心脏。" },
  },
  {
    pluginId: "fun-facts",
    toolName: "random_fun_fact",
    rendererId: "fun_facts_card",
    title: "笑话分支",
    data: { kind: "joke", content: "程序员为什么喜欢本地夹具？因为不会掉线。" },
  },
  {
    pluginId: "github-trending",
    toolName: "github_trending",
    rendererId: "github_trending_list",
    title: "GitHub 趋势",
    data: {
      items: [
        {
          name: "octop-fixture",
          full_name: "demo/octop-fixture",
          url: "https://github.com/demo/octop-fixture",
          stars: 1200,
          description: "静态本地卡片示例",
          language: "TypeScript",
        },
      ],
      since: "daily",
      language: "TypeScript",
    },
  },
  {
    pluginId: "hot-topics",
    toolName: "get_hot_topics",
    rendererId: "hot_topics_list",
    title: "微博热榜",
    data: {
      source: "weibo",
      items: [
        {
          rank: 1,
          title: "本地热榜条目",
          url: "https://example.invalid/topic",
          extra: "夹具",
        },
      ],
    },
  },
  {
    pluginId: "hot-topics",
    toolName: "get_hot_topics",
    rendererId: "hot_topics_list",
    title: "知乎热榜",
    data: {
      source: "zhihu",
      items: [
        {
          rank: 2,
          title: "本地知乎条目",
          url: "https://example.invalid/zhihu",
          extra: "夹具",
        },
      ],
    },
  },
  {
    pluginId: "hot-topics",
    toolName: "get_hot_topics",
    rendererId: "hot_topics_list",
    title: "Hacker News 热榜",
    data: {
      source: "hn",
      items: [
        {
          rank: 3,
          title: "Local Hacker News fixture",
          url: "https://example.invalid/hn",
          extra: "fixture",
        },
      ],
    },
  },
  {
    pluginId: "ip-lookup",
    toolName: "lookup_ip",
    rendererId: "ip_lookup_card",
    title: "IP 查询",
    data: {
      country: "中国",
      regionName: "浙江",
      city: "杭州",
      isp: "本地样例网络",
      query: "203.0.113.8",
    },
  },
  {
    pluginId: "market-quotes",
    toolName: "get_forex",
    rendererId: "market_quotes_card",
    title: "外汇报价",
    data: {
      kind: "forex",
      rows: [{ symbol: "USD/CNY", price: 7.12, change: 0.01, pct: 0.14 }],
      as_of: "fixture",
    },
  },
  {
    pluginId: "market-quotes",
    toolName: "get_crypto",
    rendererId: "market_quotes_card",
    title: "加密货币报价",
    data: {
      kind: "crypto",
      rows: [
        {
          symbol: "BTC",
          price: 67000,
          price_cny: 477040,
          pct: 1.2,
          change: 790,
        },
      ],
      as_of: "fixture",
    },
  },
  {
    pluginId: "market-quotes",
    toolName: "get_cn_stock",
    rendererId: "market_quotes_card",
    title: "A 股报价",
    data: {
      kind: "cn_stock",
      rows: [
        {
          symbol: "600000",
          name: "本地示例",
          price: 10.2,
          change: 0.1,
          pct: 0.99,
        },
      ],
      as_of: "fixture",
    },
  },
  {
    pluginId: "meme-maker",
    toolName: "make_meme",
    rendererId: "meme_maker_card",
    title: "表情包卡片",
    data: {
      template: "local",
      top: "本地夹具",
      bottom: "没有远程图片",
      image_url: localImage,
    },
  },
  {
    pluginId: "meme-maker",
    toolName: "list_meme_templates",
    rendererId: "meme_templates_list",
    title: "表情模板列表",
    data: { items: [{ id: "local", name: "本地模板", hint: "SVG fixture" }] },
  },
  {
    pluginId: "mini-games",
    toolName: "tic_tac_toe",
    rendererId: "mini_game_card",
    title: "井字棋",
    data: {
      kind: "tictactoe",
      board: "XO..X.O..",
      play_as: "X",
      result: null,
    },
  },
  {
    pluginId: "mini-games",
    toolName: "guess_number",
    rendererId: "mini_game_card",
    title: "猜数字",
    data: { kind: "guess", low: 1, high: 20, guess: 8, hint: "再大一点" },
  },
  {
    pluginId: "movie-search",
    toolName: "search_movie",
    rendererId: "movie_search_card",
    title: "影片搜索",
    data: {
      items: [
        {
          title: "本地影片示例",
          score: 8.1,
          url: "https://example.invalid/movie",
          image: localImage,
          summary: "静态电影卡片 fixture。",
        },
      ],
      query: "本地影片",
      source: "fixture",
    },
  },
  {
    pluginId: "parcel-tracker",
    toolName: "track_parcel",
    rendererId: "parcel_timeline",
    title: "物流轨迹",
    data: {
      number: "LOCAL-0001",
      company: "本地夹具",
      traces: [
        {
          time: "2026-10-26 10:00",
          context: "本地站点已收件",
          status: "运输中",
        },
        { time: "2026-10-26 12:00", context: "本地分拨中心", status: "已到达" },
      ],
      url: "https://example.invalid/parcel",
    },
  },
  {
    pluginId: "pomodoro",
    toolName: "start_pomodoro",
    rendererId: "timer_card",
    title: "番茄钟",
    data: {
      kind: "pomodoro",
      label: "专注示例",
      duration_sec: 1500,
      started_at: new Date().toISOString(),
      paused: true,
      remaining_sec: 1500,
    },
  },
  {
    pluginId: "pomodoro",
    toolName: "start_countdown",
    rendererId: "timer_card",
    title: "倒计时",
    data: {
      kind: "countdown",
      title: "本地倒计时",
      target_iso: futureCountdown,
    },
  },
  {
    pluginId: "qrcode",
    toolName: "make_qrcode",
    rendererId: "qrcode_card",
    title: "二维码",
    data: {
      content: "https://example.invalid/local-fixture",
      image_data_url: localImage,
    },
  },
  {
    pluginId: "server-status",
    toolName: "get_server_status",
    rendererId: "server_status",
    title: "服务器状态 · 静态演示值",
    data: {
      hostname: "fixture-host",
      os: { pretty: "Fixture OS", machine: "x64" },
      kernel: "fixture-kernel",
      cpu: { percent: 12, logical: 4 },
      memory: { percent: 42, used_h: "3.2 GiB", total_h: "8.0 GiB" },
      disk: { percent: 31, used_h: "31 GiB", total_h: "100 GiB" },
      uptime_h: 12,
    },
  },
  {
    pluginId: "short-link",
    toolName: "make_short_link",
    rendererId: "short_link_card",
    title: "短链接",
    data: {
      url: "https://example.invalid/source",
      shorturl: "https://example.invalid/short",
    },
  },
  {
    pluginId: "slack-calendar",
    toolName: "slack_calendar",
    rendererId: "slack_calendar_card",
    title: "日历提醒",
    data: {
      date: "2026-10-26",
      weekday: "一",
      days_to_weekend: 5,
      holiday_name: "本地节日夹具",
      days_to_holiday: 20,
      do: "整理待办",
      dont: "忽略休息",
    },
  },
  {
    pluginId: "sports-scores",
    toolName: "sports_scores",
    rendererId: "sports_scores_list",
    title: "体育比分",
    data: {
      items: [
        {
          event: "本地赛事",
          home: "主队",
          away: "客队",
          score: "2 - 1",
          date: "2026-10-26",
        },
      ],
      league: "fixture league",
      query: "fixture",
    },
  },
  {
    pluginId: "tetris",
    toolName: "start_tetris",
    rendererId: "tetris_game",
    title: "俄罗斯方块",
    data: { kind: "tetris" },
  },
  {
    pluginId: "travel-inspire",
    toolName: "travel_inspire",
    rendererId: "travel_inspire_card",
    title: "旅行灵感",
    data: {
      city: "京都",
      country: "日本",
      blurb: "静态旅行灵感卡片。",
      best_season: "秋季",
      tags: ["寺院", "步行"],
      mood: "slow",
    },
  },
  {
    pluginId: "unit-convert",
    toolName: "convert_unit",
    rendererId: "unit_convert_card",
    title: "单位换算",
    data: {
      value: 5,
      from_unit: "km",
      to_unit: "mi",
      result: 3.10686,
      category: "length",
    },
  },
  {
    pluginId: "weather",
    toolName: "get_weather",
    rendererId: "weather_card",
    title: "天气",
    data: {
      city: "杭州",
      country: "中国",
      latitude: 30.27,
      longitude: 120.15,
      current: {
        temp: 21,
        weather_code: 1,
        wind: 8,
        humidity: 55,
        label: "晴间多云",
        icon: "☀",
      },
      daily: [
        {
          date: "2026-10-26",
          t_max: 24,
          t_min: 16,
          precip: 0,
          weather_code: 1,
          label: "晴间多云",
          icon: "☀",
        },
      ],
    },
  },
  {
    pluginId: "what-to-eat",
    toolName: "what_to_eat",
    rendererId: "what_to_eat_card",
    title: "吃什么",
    data: { meal: "午餐", dish: "番茄鸡蛋面", spicy: false },
  },
  {
    pluginId: "wiki-summary",
    toolName: "wiki_summary",
    rendererId: "wiki_summary_card",
    title: "百科摘要",
    data: {
      title: "本地百科条目",
      extract: "此处为静态摘要夹具；没有读取 Wikipedia。",
      url: "https://example.invalid/wiki",
      thumbnail: localImage,
      query: "fixture",
      lang: "zh",
    },
  },
];
