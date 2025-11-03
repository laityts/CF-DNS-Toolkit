import glob
import os
import csv
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import threading  # 用于文件写入锁，确保线程安全
import sys
import select

# 交互式获取输入文件名
print("=" * 60)
print("代理检查工具配置")
print("=" * 60)

# 获取输入文件名
input_filename = input("请输入包含代理列表的文件名（支持.csv或.txt格式）: ").strip()
if not input_filename:
    print("必须输入文件名！")
    exit(1)

# 提取文件名（不含扩展名）
base_name = os.path.splitext(input_filename)[0]

# 创建输出文件夹
output_folder = base_name
if not os.path.exists(output_folder):
    os.makedirs(output_folder)
    print(f"已创建输出文件夹: {output_folder}")

# 根据输入文件名动态生成其他文件名（存放在输出文件夹下）
PROXY_FILE = os.path.join(output_folder, f'{base_name}.txt')  # 代理列表文件
IPTEST_CSV_FILE = os.path.join(output_folder, f'iptest_{base_name}.csv')  # iptest生成的CSV文件
IPTEST_TXT_FILE = os.path.join(output_folder, f'iptest_{base_name}.txt')  # iptest提取的代理文件

# 文件写入锁，确保多线程追加文件时不混乱
file_lock = threading.Lock()

def input_with_timeout(prompt, timeout=5, default=""):
    """
    带超时的输入函数
    :param prompt: 提示信息
    :param timeout: 超时时间（秒）
    :param default: 超时后的默认值
    :return: 用户输入或默认值
    """
    print(prompt, end='', flush=True)
    
    # 在Windows和Unix系统上使用不同的方法检测输入
    if sys.platform == "win32":
        # Windows系统
        import msvcrt
        import time
        
        start_time = time.time()
        input_str = ""
        
        while time.time() - start_time < timeout:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char == '\r':  # 回车键
                    print()  # 换行
                    return input_str
                elif char == '\x08':  # 退格键
                    if input_str:
                        input_str = input_str[:-1]
                        print('\b \b', end='', flush=True)
                else:
                    input_str += char
                    print(char, end='', flush=True)
            time.sleep(0.1)
        
        print(f"\n输入超时，使用默认值: {default}")
        return default
    else:
        # Unix系统 (Linux, macOS)
        i, _, _ = select.select([sys.stdin], [], [], timeout)
        if i:
            return sys.stdin.readline().strip()
        else:
            print(f"\n输入超时，使用默认值: {default}")
            return default

# 优选国家配置
PREFERRED_COUNTRY = input_with_timeout(
    f"请输入优选国家（直接回车则使用所有国家，10秒后默认使用''）: ",
    timeout=10,
    default=""
)

# 数据中心过滤配置
PREFERRED_DATACENTER = input_with_timeout(
    f"请输入数据中心过滤（直接回车则使用所有数据中心，10秒后默认使用''）: ",
    timeout=10,
    default=""
)

print("\n配置完成:")
print(f"  输入文件: {input_filename}")
print(f"  输出文件夹: {output_folder}")
print(f"  优选国家: '{PREFERRED_COUNTRY}'")
print(f"  数据中心: '{PREFERRED_DATACENTER}'")
print("=" * 60)

# 步骤0: 删除之前生成的旧文件
def cleanup_old_files():
    """删除之前生成的旧文件"""
    files_to_remove = [
        PROXY_FILE,
        IPTEST_CSV_FILE,
        IPTEST_TXT_FILE
    ]
    
    for file_path in files_to_remove:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"已删除旧文件: {file_path}")
        except Exception as e:
            print(f"删除文件 {file_path} 时发生异常: {str(e)}")

# 执行清理
cleanup_old_files()

# 步骤1: 从输入文件提取 ip 和 port 并保存到 {base_name}.txt
if not os.path.exists(PROXY_FILE):
    try:
        if not os.path.exists(input_filename):
            print(f"{input_filename} 不存在，无法提取代理。")
            exit(1)
        
        file_extension = os.path.splitext(input_filename)[1].lower()
        
        if file_extension == '.csv':
            # 处理CSV文件
            with open(input_filename, 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                headers = next(reader, None)  # 读取表头行
                if headers is None:
                    print("CSV文件为空。")
                    exit(1)
                
                # 查找列索引，支持多种中英文列名格式
                ip_col_idx = -1
                port_col_idx = -1
                datacenter_col_idx = -1
                
                for i, header in enumerate(headers):
                    header_lower = header.lower().strip()
                    
                    # 匹配IP相关列名
                    if header_lower in ['ip', 'ip地址', 'ip 地址', 'ip address', 'ip_address']:
                        ip_col_idx = i
                    # 匹配端口相关列名  
                    elif header_lower in ['port', '端口', '端口号']:
                        port_col_idx = i
                    # 匹配数据中心相关列名
                    elif header_lower in ['datacenter', '数据中心', '数据中心名称', 'datacenter name', 'provider']:
                        datacenter_col_idx = i
                
                # 如果没找到标准列名，尝试使用前两列作为默认
                if ip_col_idx == -1 and len(headers) > 0:
                    ip_col_idx = 0
                    print(f"未找到IP列，使用第一列 '{headers[0]}' 作为IP地址")
                if port_col_idx == -1 and len(headers) > 1:
                    port_col_idx = 1
                    print(f"未找到端口列，使用第二列 '{headers[1]}' 作为端口")
                
                if ip_col_idx == -1 or port_col_idx == -1:
                    print("CSV中未找到 'ip' 和 'port' 列（忽略大小写）。")
                    exit(1)
                
                # 检查是否设置了数据中心过滤但未找到数据中心列
                if PREFERRED_DATACENTER and datacenter_col_idx == -1:
                    print(f"警告: 设置了数据中心过滤 '{PREFERRED_DATACENTER}'，但未找到数据中心列")
                    print("可用的列名:", headers)
                    print("将继续处理所有行，不进行数据中心过滤")
                
                # 读取数据行并写入 {base_name}.txt
                valid_count = 0
                filtered_count = 0
                with open(PROXY_FILE, 'w', encoding='utf-8') as f:
                    for row in reader:
                        if len(row) > max(ip_col_idx, port_col_idx):
                            ip = row[ip_col_idx].strip()
                            port = row[port_col_idx].strip()
                            
                            # 检查数据中心过滤条件
                            if PREFERRED_DATACENTER and datacenter_col_idx != -1:
                                if len(row) > datacenter_col_idx:
                                    datacenter = row[datacenter_col_idx].strip()
                                    # 如果设置了数据中心过滤且当前行不匹配，则跳过
                                    if datacenter != PREFERRED_DATACENTER:
                                        filtered_count += 1
                                        continue
                            
                            # 直接写入，不做验证
                            if ip and port:
                                f.write(f"{ip} {port}\n")
                                valid_count += 1
                
                print(f"已将 {valid_count} 个IPs和ports提取到 {PROXY_FILE}")
                if PREFERRED_DATACENTER and datacenter_col_idx != -1:
                    print(f"根据数据中心 '{PREFERRED_DATACENTER}' 过滤掉了 {filtered_count} 行")
                    
        elif file_extension == '.txt':
            # 处理TXT文件，假设格式为 "ip port" 或 "ip:port"
            valid_count = 0
            with open(input_filename, 'r', encoding='utf-8') as infile, \
                 open(PROXY_FILE, 'w', encoding='utf-8') as outfile:
                for line in infile:
                    line = line.strip()
                    if not line:
                        continue
                    
                    # 处理 "ip port" 或 "ip:port" 格式
                    if ' ' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            ip, port = parts[0], parts[1]
                        else:
                            continue
                    elif ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            ip, port = parts[0], parts[1]
                        else:
                            continue
                    else:
                        continue
                    
                    # 直接写入，不做验证
                    if ip and port:
                        outfile.write(f"{ip} {port}\n")
                        valid_count += 1
            
            if valid_count == 0:
                print("TXT文件中无IP和端口数据。")
                exit(1)
            
            print(f"已将 {valid_count} 个IPs和ports从 {input_filename} 提取到 {PROXY_FILE}")
            if PREFERRED_DATACENTER:
                print("注意: TXT文件格式不支持数据中心过滤，已忽略数据中心设置")
        else:
            print(f"不支持的文件格式: {file_extension}，请使用.csv或.txt文件")
            exit(1)
            
    except FileNotFoundError:
        print(f"文件 {input_filename} 不存在。")
        exit(1)
    except csv.Error as e:
        print(f"读取CSV文件时发生错误: {str(e)}")
        exit(1)
    except Exception as e:
        print(f"提取代理时发生异常: {str(e)}")
        exit(1)

# 步骤2: 执行 ./iptest 并处理生成的 CSV
print("正在执行 ./iptest 命令...")
try:
    # 构建iptest命令
    iptest_command = ['./iptest', '-file', PROXY_FILE, '-outfile', IPTEST_CSV_FILE, '-tls=true']
    
    # 执行iptest命令
    process = subprocess.Popen(iptest_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    
    # 实时读取并显示输出
    print("=" * 50)
    print("iptest 执行输出:")
    print("=" * 50)
    
    while True:
        output = process.stdout.readline()
        if output == '' and process.poll() is not None:
            break
        if output:
            print(output.strip())
    
    returncode = process.poll()
    
    if returncode != 0:
        print(f"执行 ./iptest 失败，返回码: {returncode}")
    else:
        print("./iptest 执行成功")
        
        # 检查是否生成了 CSV 文件
        if os.path.exists(IPTEST_CSV_FILE):
            print(f"检测到 {IPTEST_CSV_FILE} 文件，开始提取代理信息...")
            
            # 从 iptest CSV 提取 ip 和端口，保存到 iptest_{base_name}.txt
            seen_proxies = set()  # 用于去重的集合
            valid_count = 0
            with open(IPTEST_CSV_FILE, 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                headers = next(reader, None)  # 读取表头行
                
                if headers and len(headers) >= 9:  # 确保有足够的列
                    # 查找IP、端口和国家列的位置
                    ip_col_idx = 0
                    port_col_idx = 1
                    country_col_idx = 8  # 国家在第9列（0-indexed）
                    
                    # 写入 iptest_{base_name}.txt，根据优选国家过滤，并去重
                    with open(IPTEST_TXT_FILE, 'w', encoding='utf-8') as f:
                        for row in reader:
                            if len(row) > max(ip_col_idx, port_col_idx, country_col_idx):
                                ip = row[ip_col_idx].strip()
                                port = row[port_col_idx].strip()
                                country = row[country_col_idx].strip()
                                
                                # 根据是否设置了优选国家来决定过滤条件
                                if ip and port:
                                    if not PREFERRED_COUNTRY or country == PREFERRED_COUNTRY:
                                        proxy_key = f"{ip}:{port}"  # 创建唯一标识符
                                        if proxy_key not in seen_proxies:  # 检查是否重复
                                            seen_proxies.add(proxy_key)
                                            f.write(f"{ip} {port}\n")
                                            valid_count += 1
                    
                    if PREFERRED_COUNTRY:
                        print(f"从 {IPTEST_CSV_FILE} 提取了 {valid_count} 个优选国家 '{PREFERRED_COUNTRY}' 的代理到 {IPTEST_TXT_FILE}")
                    else:
                        print(f"从 {IPTEST_CSV_FILE} 提取了 {valid_count} 个所有国家的代理到 {IPTEST_TXT_FILE}")
                else:
                    print(f"{IPTEST_CSV_FILE} 文件格式不正确")
        else:
            print(f"未找到 {IPTEST_CSV_FILE} 文件")
            
except subprocess.TimeoutExpired:
    print("./iptest 执行超时")
except FileNotFoundError:
    print("未找到 ./iptest 命令")
except Exception as e:
    print(f"执行 ./iptest 时发生异常: {str(e)}")

print("\n" + "="*80)
print("代理处理完成！")
print("="*80)
print(f"原始代理列表: {PROXY_FILE}")
print(f"iptest 结果CSV: {IPTEST_CSV_FILE}")
if os.path.exists(IPTEST_TXT_FILE):
    with open(IPTEST_TXT_FILE, 'r', encoding='utf-8') as f:
        proxy_count = len([line for line in f if line.strip()])
    print(f"过滤后的代理列表 ({proxy_count} 个): {IPTEST_TXT_FILE}")
print("="*80)