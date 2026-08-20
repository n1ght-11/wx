name: wxread-draft-test

permissions:
  contents: read
  issues: write

on:
  workflow_dispatch:
    inputs:
      minutes:
        description: '测试阅读时长（分钟）'
        required: false
        default: '10'
  push:
    branches: [draft-test]

jobs:
  read:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Playwright
        run: |
          python -m pip install --upgrade pip
          pip install playwright
          playwright install chromium
          playwright install-deps

      - name: Run draft reader (v4 防卡末页 · 信标停滞跳章)
        id: reader
        env:
          WXREAD_COOKIES: ${{ secrets.WXREAD_COOKIES }}
          WXREAD_BOOK: ${{ secrets.WXREAD_BOOK }}
          WXREAD_MINUTES: ${{ github.event.inputs.minutes || '10' }}
        run: python main_draft.py

      - name: 微信推送 - 测试日报（Server酱）
        if: always()
        env:
          SC_KEY: ${{ secrets.SC_SENDKEY }}
          MINUTES: ${{ steps.reader.outputs.minutes }}
          SENT: ${{ steps.reader.outputs.sent }}
          ACCEPTED: ${{ steps.reader.outputs.accepted }}
          STATUS: ${{ steps.reader.outputs.status }}
          ERR_CODES: ${{ steps.reader.outputs.err_codes }}
          READINFO: ${{ steps.reader.outputs.readinfo }}
        run: |
          if [ -z "$SC_KEY" ]; then
            echo "SC_SENDKEY 未设置，跳过微信推送"
            exit 0
          fi
          NOW=$(date -u -d '+8 hours' '+%Y-%m-%d %H:%M')
          desp="**测试运行时间：** ${NOW}
          **目标时长：**【${MINUTES:-?}】分钟
          **状态：** ${STATUS:-?}
          **阅读请求：** 发送 ${SENT:-?} / 后台接受 ${ACCEPTED:-?}
          **errCode 分布：** \`${ERR_CODES:-n/a}\`

          [查看运行日志](${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }})"
          curl -s -X POST "https://sctapi.ftqq.com/${SC_KEY}.send" \
            --data-urlencode "title=🧪 草稿测试·状态${STATUS:-?}·发送${SENT:-?}/接受${ACCEPTED:-?}" \
            --data-urlencode "desp=${desp}"
          echo "微信推送已发送"

      - name: 结果写回 Issue（供核对）
        if: always()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const out = {};
            try {
              const raw = fs.readFileSync(process.env.GITHUB_OUTPUT, 'utf8');
              for (const line of raw.split('\n')) {
                const idx = line.indexOf('=');
                if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              }
            } catch (e) { out.readerr = String(e); }
            const body = `**草稿(v4 防卡末页 · 信标停滞跳章) 测试结果**
            - 状态: \`${out.status || '?'}\`
            - 目标时长: ${out.minutes || '?'} 分钟
            - 发送: ${out.sent || '?'} / 接受: ${out.accepted || '?'}
            - errCode: \`${out.err_codes || 'n/a'}\`
            - readInfo: ${out.readinfo || 'n/a'}
            [日志](${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId})`;
            await github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: '🧪 草稿测试运行结果',
              body
            });

# trigger-redeploy: 2026-08-20T20:24:37
