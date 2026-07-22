import sys
import os
import base64
from Crypto.Cipher import AES

def pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return b""
    padding_len = data[-1]
    if padding_len < 1 or padding_len > 16:
        return data
    for i in range(1, padding_len + 1):
        if data[-i] != padding_len:
            return b""
    return data[:-padding_len]


def decrypt_line(ciphertext_b64: str, key_str: str, iv_str: str) -> str:
    try:
        ciphertext_b64 = ciphertext_b64.strip()
        if not ciphertext_b64:
            return ""

        raw_ciphertext = base64.b64decode(ciphertext_b64)
        cipher = AES.new(
            key_str.encode("utf-8")[:16],
            AES.MODE_CBC,
            iv=iv_str.encode("utf-8")[:16],
        )

        decrypted_raw = cipher.decrypt(raw_ciphertext)
        unpadded = pkcs7_unpad(decrypted_raw)
        return unpadded.decode("utf-8", errors="ignore")
    except Exception as e:
        return ""


def decrypt_module_file(file_path: str, key: str, iv: str) -> str:
    if not os.path.exists(file_path):
        return f"[!] Error: Khong tim thay file: '{file_path}'"

    decrypted_lines = []

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            plain_line = decrypt_line(line, key, iv)
            if plain_line:
                decrypted_lines.append(plain_line)

    full_code = "\n".join(decrypted_lines)
    return full_code

def parse_args():
    args_dict = {}
    for arg in sys.argv[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            args_dict[k.strip().lower()] = v.strip()
    return args_dict

if __name__ == "__main__":
    args = parse_args()

    input_file = args.get("input", "")
    output_file = args.get("output", "")
    key = args.get("key", "DEFAULT_KEY")
    iv = args.get("iv", "DEFAULT_IV")

    print(f"[*] Dang giai ma file : {input_file}")
    result = decrypt_module_file(input_file, key, iv)

    print(f"[*] Ghi ket qua ra file : {output_file}")
    with open(output_file, "w", encoding="utf-8") as f_out:
        f_out.write(result)