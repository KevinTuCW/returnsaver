#!/usr/bin/env bash
# Return Saver 全流程演示：五阶段 × 五场景 × 四层护栏 × 路由经济性
# 用法：bash demo.sh
set -u
BASE=${BASE:-http://localhost:8777}
PY=${PY:-.venv/bin/python}
# 本机代理会劫持 localhost，必须绕过。写成函数，避免 '*' 被 shell 展开成文件名
c() { curl -s --noproxy '*' "$@"; }

j() { $PY -c "import sys,json;d=sys.stdin.read();b,_,h=d.partition('[HTTP');print(json.dumps(json.loads(b),ensure_ascii=False,indent=2));print('[HTTP'+h if h else '')"; }

post() { c -w "\n[HTTP %{http_code}]" -X POST "$BASE$1" -H 'Content-Type: application/json' -d "$2"; }

# 走完 S1→S3→S5：第一次拿 session + 确认列表，第二次带 confirm_order_id 推进
flow() {  # flow <标题> <customer> <order> <message> [extra-json]
  echo; echo "═══════════ $1"
  local extra=${5:-}
  local first sid
  first=$(post /api/negotiate "{\"customer_id\":\"$2\",\"message\":\"$4\"}" )
  sid=$(echo "$first" | $PY -c "import sys,json;print(json.loads(sys.stdin.read().split('[HTTP')[0])['session_id'])")
  echo "--- S3 确认订单（节选）"
  echo "$first" | $PY -c "
import sys,json;d=json.loads(sys.stdin.read().split('[HTTP')[0])
print(' stage:',d['stage'],'| status:',d['status'])
print(' 候选:',[c['order_id']+' '+c['product'] for c in d.get('candidates',[])])"
  echo "--- S5 执行"
  post /api/negotiate "{\"session_id\":\"$sid\",\"customer_id\":\"$2\",\"message\":\"$4\",\"confirm_order_id\":\"$3\"${extra:+,$extra}}" | j
}

echo "═══════════ health"; c "$BASE/health" | $PY -m json.tool

flow "① 不满足售后规则 → 附规则原文婉拒 + 给台阶（零 LLM 成本）" C-003 ORD-1003 "I want to return this jacket"
flow "② 产品使用问题 → 手册 + 上手技巧 + 延长窗口（不发钱）" C-004 ORD-1004 "I cannot get it to pair, how to connect?"
flow "③ 价格价值不符 → 换货/补偿阶梯（≤30% 硬上限）" C-001 ORD-1001 "It is too small, I want a return"
flow "④ 产品损坏 → 引导维修或换货（豁免窗口限制）" C-002 ORD-1002 "The mug arrived cracked"
flow "⑤a 情绪激烈 + 小额 → 立即退款 + 回头钩子（禁止再挽留）" C-005 ORD-1005 "This is ridiculous, just refund me NOW!"
flow "⑤b 情绪激烈 + 大额 → 人工介入 + 死时效 SLA" C-006 ORD-1006 "This is unacceptable, I want my money back NOW!"

flow "⑥ 【L2 护栏】LLM 编造白名单外的 offer" C-001 ORD-1001 "It is too small, I want a return" '"force":"bad_offer"'
flow "⑦ 【L3 护栏】话术里出现越权承诺" C-001 ORD-1001 "It is too small, I want a return" '"force":"bad_text"'
flow "⑧ 【L3 护栏】话术里出现未批准金额" C-001 ORD-1001 "It is too small, I want a return" '"force":"bad_amount"'

echo; echo "═══════════ ⑨ 【L4】签名 token 执行 + 幂等 + 伪造拦截"
FIRST=$(post /api/negotiate '{"customer_id":"C-001","message":"It is too small, I want a return"}')
SID=$(echo "$FIRST" | $PY -c "import sys,json;print(json.loads(sys.stdin.read().split('[HTTP')[0])['session_id'])")
TOK=$(post /api/negotiate "{\"session_id\":\"$SID\",\"customer_id\":\"C-001\",\"message\":\"too small\",\"confirm_order_id\":\"ORD-1001\"}" | $PY -c "import sys,json;print(json.loads(sys.stdin.read().split('[HTTP')[0])['offer_token'])")
K="k-$RANDOM$RANDOM"
echo "-- 正常执行";   post /api/accept "{\"offer_token\":\"$TOK\",\"idempotency_key\":\"$K\"}" | j
echo "-- 幂等重放";   post /api/accept "{\"offer_token\":\"$TOK\",\"idempotency_key\":\"$K\"}" | j
FAKE=$($PY -c "
import json,time
p={'order_id':'ORD-1001','offer_id':'FULL_REFUND_200','value':999.0,'exp':int(time.time())+900,'jti':'x'}
print(json.dumps(p,separators=(',',':'),sort_keys=True).encode().hex()+'.'+'deadbeef'*4)")
echo "-- 伪造 token（金额改成 999）"; post /api/accept "{\"offer_token\":\"$FAKE\",\"idempotency_key\":\"k-evil-1\"}" | j

echo; echo "═══════════ ⑩ 体验出口：用户随时可放弃挽留"
F=$(post /api/negotiate '{"customer_id":"C-001","message":"I want to return"}')
S=$(echo "$F" | $PY -c "import sys,json;print(json.loads(sys.stdin.read().split('[HTTP')[0])['session_id'])")
post /api/negotiate "{\"session_id\":\"$S\",\"customer_id\":\"C-001\",\"message\":\"no thanks\",\"want_return_anyway\":true}" | j

echo; echo "═══════════ ⑪ 人工工单队列"; c "$BASE/api/manual-queue" | $PY -m json.tool
echo; echo "═══════════ ⑫ PM 看板指标（成本 / 双口径挽留率 / 路由分布）"; c "$BASE/api/metrics" | $PY -m json.tool
echo; echo "═══════════ 护栏触发日志"; grep -E "GUARDRAIL|EXPERIENCE" /tmp/rs.log | tail -20 || echo "(无)"
