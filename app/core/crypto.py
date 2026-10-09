"""PII 库内加密（T176，D082/D081 第 2 层）：确定性 AES-256-CBC。

目标：`policies.holder_id_card` / `medical_records.patient_id_card` 密文落库——
DB 泄漏（拖库/备份外流）不直接等于 PII 泄漏；同时**等值查询可用**（policy_query /
fraud history / record_query 均按证件号等值过滤）。

方案：确定性加密——IV = HMAC-SHA256(mac_key, 明文)[:16]，同明文恒同密文
（等值可匹配），不同明文 IV 不同；密文格式 `v1:<base64(iv + ct)>`：
- `v1:` 前缀让迁移与解密可区分密文与未迁移明文（decrypt 对非前缀值原样返回，
  滚动迁移期两态共存）
- 前缀同时是版本位——未来算法升级发 v2 双写迁移

密钥管理：`PII_ENCRYPTION_KEY` 环境变量（prod 强制非空，config 校验）；
AES/HMAC 密钥经 sha256 派生。dev 未配置时用固定开发密钥（警告一次）——
本地零配置可跑，但 dev 密文不具保密性。

依赖说明（D082）：引入 `cryptography` 是本项目第一个"纯加密"第三方依赖——
stdlib 无 AES； keyed-hash 伪匿化方案（stdlib 可实现）不可逆，迁移后原始
证件号永久丢失，且保单核验/客服场景需要可恢复明文（受控读取）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_PREFIX = "v1:"
_DEV_KEY = "claimflow-dev-only-pii-key"

_warned_dev_key = False


def _derive_keys() -> tuple[bytes, bytes]:
    """派生 (AES 密钥, HMAC 密钥)；dev 未配置时固定开发密钥（警告一次）。"""
    global _warned_dev_key
    raw = settings.pii_encryption_key
    if not raw:
        if not _warned_dev_key:
            log.warning(
                "pii_encryption_key_missing_dev_fallback",
                hint="dev 使用固定开发密钥，密文不具保密性；prod 强制配置 PII_ENCRYPTION_KEY",
            )
            _warned_dev_key = True
        raw = _DEV_KEY
    aes_key = hashlib.sha256((raw + ":aes").encode("utf-8")).digest()
    mac_key = hashlib.sha256((raw + ":mac").encode("utf-8")).digest()
    return aes_key, mac_key


def encrypt_pii(plaintext: str) -> str:
    """加密为 `v1:<base64(iv+ct)>`（确定性：同明文恒同密文）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    aes_key, mac_key = _derive_keys()
    data = plaintext.encode("utf-8")
    iv = hmac.new(mac_key, data, hashlib.sha256).digest()[:16]
    # PKCS7 对齐（16 字节块）：恒为 1-16 字节填充，短值也占 ≥1 块
    padded = data + bytes([16 - len(data) % 16]) * (16 - len(data) % 16)
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return _PREFIX + base64.b64encode(iv + ct).decode("ascii")


def decrypt_pii(stored: str) -> str:
    """解密 `v1:` 密文；非前缀值（未迁移明文 / 手写值）原样返回。

    密钥不符 → 填充校验失败抛 ValueError（fail loud：密钥轮换后静默错读
    比读不到更糟）。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not stored.startswith(_PREFIX):
        return stored
    raw = base64.b64decode(stored[len(_PREFIX) :])
    iv, ct = raw[:16], raw[16:]
    aes_key, _ = _derive_keys()
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    pad_len = padded[-1]
    if not 1 <= pad_len <= 16 or padded[-pad_len:] != bytes([pad_len]) * pad_len:
        msg = "PII 密文解密失败（密钥不符或数据损坏）"
        raise ValueError(msg)
    return padded[:-pad_len].decode("utf-8")


class EncryptedString(TypeDecorator):
    """确定性加密字符串列（T176）：写入加密、读取解密，等值查询可匹配。

    ORM 全链路透明——种子/工具/评测的读写代码零改动；raw SQL 读到的是
    `v1:` 密文（迁移脚本据此区分两态）。
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001
        return dialect.type_descriptor(String(128))

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return encrypt_pii(str(value))

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return decrypt_pii(str(value))
