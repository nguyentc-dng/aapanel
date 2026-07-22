import sys
import os
import base64
from Crypto.Cipher import AES

def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    padding_len = block_size - (len(data) % block_size)
    padding = bytes([padding_len] * padding_len)
    return data + padding


def encrypt_line(plain_text: str, key_str: str, iv_str: str) -> str:
    if not plain_text:
        return ""

    key_bytes = key_str.encode("utf-8")[:16]
    iv_bytes = iv_str.encode("utf-8")[:16]

    data_bytes = plain_text.encode("utf-8")
    padded_data = pkcs7_pad(data_bytes, 16)

    cipher = AES.new(key_bytes, AES.MODE_CBC, iv=iv_bytes)
    encrypted_bytes = cipher.encrypt(padded_data)

    return base64.b64encode(encrypted_bytes).decode("utf-8")


def encrypt_module_file(file_path: str, key: str, iv: str) -> str:
    if not os.path.exists(file_path):
        return f"[!] Error: Khong tim thay file: '{file_path}'"

    encrypted_lines = []

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line:
                continue

            encrypted_line = encrypt_line(line, key, iv)
            if encrypted_line:
                encrypted_lines.append(encrypted_line)

    full_encrypted = "\n".join(encrypted_lines)
    return full_encrypted

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
    result = encrypt_module_file(input_file, key, iv)

    print(f"[*] Ghi ket qua ra file : {output_file}")
    with open(output_file, "w", encoding="utf-8") as f_out:
        f_out.write(result)
