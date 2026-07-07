# ETF指数四维量化评分体系 - Skill配置

## 基本信息
- **名称**: ETF指数四维量化评分
- **版本**: V5
- **描述**: 基于四维度等权评分模型（质量/资金面/技术面/消息面），对A股主要指数进行0-10分量化评分，生成HTML可视化报告
- **作者**: cyz87687
- **仓库**: https://github.com/cyz87687/etf-scoring
- **在线报告**: https://cyz87687.github.io/etf-scoring/index_v5.html

## 评分体系

### 四维度（各25%权重）
| 维度 | 权重 | 子因子 | 数据来源 |
|------|------|--------|----------|
| 质量因子 | 25% | PEG代理(PE百分位)、ROE代理(PB百分位)、增速代理(PS百分位)、纯度代理(股息率百分位) | 东方财富/腾讯财经 |
| 资金因子 | 25% | 主力代理(板块5日资金流入)、北向代理(ETF申赎)、融资代理(指数换手率)、机构代理(ETF份额变化) | 新浪/东方财富/腾讯财经 |
| 技术因子 | 25% | 趋势(60日动量)、量价(量比) | 腾讯财经 |
| 消息因子 | 25% | 景气代理(板块涨跌)、舆情代理(新闻关注度) | 新浪/腾讯财经 |

### 评级标准
| 总分 | 评级 | 含义 |
|------|------|------|
| 9.0-10.0 | 优质配置 | 强烈推荐 |
| 7.0-8.9 | 中性偏多 | 积极关注 |
| 5.0-6.9 | 均衡观望 | 持有观察 |
| 3.0-4.9 | 谨慎规避 | 减仓回避 |
| 0.0-2.9 | 坚决规避 | 远离止损 |

## 使用方式

### 本地运行（需westock-data）
```bash
# 1. 先用westock-data获取数据
westock-data quote -c cs930598,sz399960,... > /tmp/v3_quote.txt
westock-data etf -c sz159713,... > /tmp/v3_etf.txt
westock-data board > /tmp/v3_board.txt
westock-data news > /tmp/v3_news.txt
westock-data hot > /tmp/v3_hot.txt

# 2. 运行评分模型
python build_v4.py
```

### 纯API模式（无需westock-data，适用于CI/CD）
```bash
python build_v4.py --api
```

### 输出文件
- `index_v5.html` - HTML可视化报告
- `v5_data.json` - JSON评分数据

## 自动更新

### GitHub Actions定时任务
- **触发时间**: 每个交易日 15:30（北京时间）
- **Cron表达式**: `30 7 * * 1-5`（UTC时间）
- **部署目标**: GitHub Pages (https://cyz87687.github.io/etf-scoring/)
- **手动触发**: 支持在GitHub Actions页面手动运行

### 工作流文件
`.github/workflows/daily-update.yml`

## 依赖
- Python 3.11+
- pandas
- openpyxl
- requests

## 项目结构
```
etf-scoring/
├── build_v4.py                    # 核心评分模型（主程序）
├── fetch_data.py                  # 纯API数据获取模块
├── 指数数据.xlsx                   # 指数列表配置
├── 指数四维精简量化评分体系（可落地·API适配）.md  # 评分体系文档
├── index_v5.html                  # 生成的HTML报告
├── v5_data.json                   # 生成的JSON数据
├── .github/workflows/
│   └── daily-update.yml           # GitHub Actions定时任务
└── .gitignore
```

## 数据源
| 数据 | API | 说明 |
|------|-----|------|
| 指数行情/估值 | 东方财富push2 API | PE/PB/涨跌幅 |
| 指数换手率 | 腾讯财经qt.gtimg.cn | 部分指数有换手率 |
| ETF行情/申赎 | 腾讯财经qt.gtimg.cn | 换手率/涨跌/份额 |
| 行业板块 | 新浪财经/东方财富 | 涨跌/资金流入 |
| 新闻 | 腾讯财经 | 市场新闻 |

## 注意事项
1. API数据可能有延迟，建议15:30后运行（A股收盘后）
2. 部分指数（港股通、恒生科技）数据覆盖有限
3. PEG/ROE等因子使用PE/PB百分位作为代理指标
4. 评分基于横截面排名，非绝对值，反映相对强弱
