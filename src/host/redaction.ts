/**
 * 日志与错误脱敏器（设计 §5 三条硬规则：无栈、无 token、无完整带凭据 URL）。
 * 纯函数集合，无副作用——host 侧所有 logger 输出与错误消息上抛前过这里。
 */

/** 掩码规则：长度 <=8 全掩；>8 保留前 4 字符 + '****'。 */
export function maskSecret(secret: string): string {
  if (secret.length <= 8) return '****';
  return `${secret.slice(0, 4)}****`;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 已知 secret 值在文本中的全部出现替换为各自掩码（规则二：token 只记结果，不回显）。 */
export function redactText(text: string, secrets: readonly string[]): string {
  let result = text;
  for (const secret of secrets) {
    if (!secret) continue;
    result = result.replace(new RegExp(escapeRegExp(secret), 'g'), maskSecret(secret));
  }
  return result;
}

/** 敏感键名命中集：键名小写并剥除非字母数字后做子串匹配。 */
const SENSITIVE_KEY_FRAGMENTS = ['secret', 'password', 'apikey', 'authorization', 'token'] as const;

function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
  return SENSITIVE_KEY_FRAGMENTS.some((fragment) => normalized.includes(fragment));
}

/** 深遍历对象/数组，敏感键的值替换为 '[redacted]'。纯函数：返回新结构。 */
export function redactKeys<T>(value: T): T {
  return redactValue(value) as T;
}

function redactValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => redactValue(item));
  if (value !== null && typeof value === 'object') {
    const output: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      output[key] = isSensitiveKey(key) ? '[redacted]' : redactValue(item);
    }
    return output;
  }
  return value;
}

/** 完整带凭据 URL 打码：userinfo（user:pass@）与查询参数中的 token 一律遮盖。 */
export function redactUrlCredentials(url: string): string {
  const noUserinfo = url.replace(/\/\/[^/@\s]+@/g, '//****@');
  // 遮盖常见敏感查询参数的值：token / access_token / key / secret / password / apikey 等。
  return noUserinfo.replace(/([?&](?:access_token|token|key|secret|password|apikey|api_key|auth|sig|signature)=)[^&\s#]*/gi, '$1****');
}

/** 栈剥离：取 Error.message，绝不透传 .stack。 */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

/** 错误消息截断（规则三：超长错误截断 500 字符）。 */
export function truncateMessage(message: string, max = 500): string {
  return message.length <= max ? message : `${message.slice(0, max)}…`;
}

/** 已知 secret 注册表：生产代码在拿到凭据时登记，所有脱敏调用统一消费。 */
const knownSecrets: string[] = [];

/** 登记已知 secret 值（幂等去重）。后续 redactError/redactText 默认覆盖。 */
export function addSecrets(secrets: readonly string[]): void {
  for (const secret of secrets) {
    if (secret && !knownSecrets.includes(secret)) knownSecrets.push(secret);
  }
}

/** 组合脱敏：剥栈、去 URL 凭据、去已知 secret，再截断。 */
export function redactError(error: unknown, extraSecrets: readonly string[] = []): string {
  const base = redactUrlCredentials(errorMessage(error));
  return truncateMessage(redactText(base, [...knownSecrets, ...extraSecrets]));
}
