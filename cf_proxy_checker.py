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
SUCCESS_PROXY_FILE = os.path.join(output_folder, f'{base_name}_success.txt')  # 成功代理保存文件
FULL_RESPONSES_FILE = os.path.join(output_folder, f'{base_name}.log')  # 完整响应保存文件
IPTEST_CSV_FILE = os.path.join(output_folder, f'iptest_{base_name}.csv')  # iptest生成的CSV文件
IPTEST_TXT_FILE = os.path.join(output_folder, f'iptest_{base_name}.txt')  # iptest提取的代理文件
PREFERRED_PROXY_FILE = os.path.join(output_folder, f'{base_name}_preferred.txt')  # 优选代理保存文件

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

# 优选反代最大响应时间配置
try:
    response_time_input = input_with_timeout(
        f"请输入优选反代的最大响应时间阈值（毫秒，10秒后默认使用350）: ",
        timeout=10,
        default="350"
    )
    PREFERRED_MAX_RESPONSE_TIME = int(response_time_input)
except ValueError:
    print(f"输入无效，使用默认值: 350")
    PREFERRED_MAX_RESPONSE_TIME = 350

# 优选反代端口配置
PREFERRED_PROXY_PORT = input_with_timeout(
    f"请输入优选反代端口（多个端口用逗号分隔，直接回车则使用所有端口，10秒后默认使用''）: ",
    timeout=10,
    default=""
)

print("\n配置完成:")
print(f"  输入文件: {input_filename}")
print(f"  输出文件夹: {output_folder}")
print(f"  优选国家: '{PREFERRED_COUNTRY}'")
print(f"  最大响应时间: {PREFERRED_MAX_RESPONSE_TIME}ms")
print(f"  优选端口: '{PREFERRED_PROXY_PORT}'")
print("=" * 60)

# 步骤0: 删除之前生成的旧文件
def cleanup_old_files():
    """删除之前生成的旧文件"""
    files_to_remove = [
        PROXY_FILE,
        SUCCESS_PROXY_FILE,
        FULL_RESPONSES_FILE,
        IPTEST_CSV_FILE,
        IPTEST_TXT_FILE,
        PREFERRED_PROXY_FILE
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
                
                for i, header in enumerate(headers):
                    header_lower = header.lower().strip()
                    
                    # 匹配IP相关列名
                    if header_lower in ['ip', 'ip地址', 'ip 地址', 'ip address', 'ip_address']:
                        ip_col_idx = i
                    # 匹配端口相关列名  
                    elif header_lower in ['port', '端口', '端口号']:
                        port_col_idx = i
                
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
                
                # 读取数据行并写入 {base_name}.txt
                valid_count = 0
                with open(PROXY_FILE, 'w', encoding='utf-8') as f:
                    for row in reader:
                        if len(row) > max(ip_col_idx, port_col_idx):
                            ip = row[ip_col_idx].strip()
                            port = row[port_col_idx].strip()
                            
                            # 直接写入，不做验证
                            if ip and port:
                                f.write(f"{ip} {port}\n")
                                valid_count += 1
                
                if valid_count == 0:
                    print("CSV中无IP和端口数据。")
                    exit(1)
                
                print(f"已将 {valid_count} 个IPs和ports提取到 {PROXY_FILE}")
                    
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

# 步骤3: 读取 iptest_{base_name}.txt 中的代理列表
proxies = []
proxy_source_file = IPTEST_TXT_FILE if os.path.exists(IPTEST_TXT_FILE) else PROXY_FILE

try:
    if os.path.exists(proxy_source_file):
        with open(proxy_source_file, 'r', encoding='utf-8') as f:
            proxies = [line.strip() for line in f if line.strip() and len(line.split()) == 2]
    if not proxies:
        print(f"{proxy_source_file} 中无有效代理，将退出。")
        exit(1)
    print(f"从 {proxy_source_file} 读取了 {len(proxies)} 个代理")
except FileNotFoundError:
    print(f"文件 {proxy_source_file} 不存在。")
    exit(1)
except Exception as e:
    print(f"读取 {proxy_source_file} 时发生异常: {str(e)}")
    exit(1)

# 清空日志文件（开始新检查）
try:
    with open(FULL_RESPONSES_FILE, 'w', encoding='utf-8'):
        pass
except Exception as e:
    print(f"清空 {FULL_RESPONSES_FILE} 时发生异常: {str(e)}")
    exit(1)

# 步骤4: 定义检查单个代理的函数
def check_proxy(ip_port):
    """
    检查单个代理的有效性，并保存完整响应信息
    """
    try:
        ip, port = ip_port.split()  # 假设格式正确，否则跳过
    except ValueError as e:
        with file_lock:
            try:
                with open(FULL_RESPONSES_FILE, 'a', encoding='utf-8') as full_file:
                    full_file.write(f"\n--- 代理: {ip_port} ---\n")
                    full_file.write(f"无效代理格式: {str(e)}\n\n")
            except Exception as write_e:
                print(f"写入 {FULL_RESPONSES_FILE} 时发生异常: {str(write_e)}")
        return None
    
    url = f"https://check.proxyip.eytan.qzz.io/check?proxyip={ip}:{port}"
    header = f"{ip}:{port}"
    stdout = ""
    stderr = ""
    returncode = 1
    
    try:
        # 使用subprocess运行curl，设置超时10秒
        result = subprocess.run(['curl', '-s', url], capture_output=True, text=True, timeout=10)
        returncode = result.returncode
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        
        # 尝试解析JSON，更新header
        if stdout:
            data = json.loads(stdout)
            header_ip = data.get('proxyIP', ip)
            header_port = str(data.get('portRemote', port))
            header = f"{header_ip}:{header_port}"
    except subprocess.TimeoutExpired:
        stderr = "请求超时"
    except json.JSONDecodeError:
        stderr = "JSON解析失败"
    except subprocess.CalledProcessError as e:
        stderr = f"subprocess调用错误: {str(e)}"
    except Exception as e:
        stderr = f"异常: {str(e)}"
    
    # 使用锁安全追加到日志文件
    with file_lock:
        try:
            with open(FULL_RESPONSES_FILE, 'a', encoding='utf-8') as full_file:
                full_file.write(f"\n--- 代理: {header} ---\n")
                full_file.write("检查结果:\n")
                full_file.write(f"STDOUT: {stdout}\n")
                full_file.write(f"STDERR: {stderr}\n")
                full_file.write(f"Return Code: {returncode}\n\n")
        except Exception as write_e:
            print(f"写入 {FULL_RESPONSES_FILE} 时发生异常: {str(write_e)}")
    
    # 检查是否成功
    success = False
    response_time = -1
    if returncode == 0 and stdout:
        try:
            data = json.loads(stdout)
            success = data.get('success', False)
            response_time = data.get('responseTime', -1)
        except json.JSONDecodeError:
            pass
    
    if success and response_time != -1:
        proxy_entry = f"{header}#{response_time}ms"
        return (response_time, proxy_entry)
    
    return None

# 步骤5: 使用 ThreadPoolExecutor 进行并发检查
try:
    max_workers = min(10, len(proxies))  # 限制最大线程数，避免资源耗尽
    successful_proxies = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_proxy = {executor.submit(check_proxy, proxy): proxy for proxy in proxies}
        with tqdm(total=len(proxies), desc="正在检查代理", unit="个") as pbar:
            for future in as_completed(future_to_proxy):
                try:
                    result = future.result()
                    if result:
                        successful_proxies.append(result)
                except Exception as e:
                    print(f"处理代理 {future_to_proxy[future]} 时发生异常: {str(e)}")
                pbar.update(1)
except Exception as e:
    print(f"并发检查代理时发生异常: {str(e)}")
    exit(1)

# 按 responseTime 排序（从小到大）
successful_proxies.sort(key=lambda x: x[0])

# 步骤6: 保存成功代理到 {base_name}_success.txt
try:
    # 先按端口从小到大排，端口相同则按响应时间从小到大排
    def get_port_and_time(proxy_entry):
        """从代理条目中提取端口和响应时间"""
        try:
            ip_port_part = proxy_entry[1].split('#')[0]
            _, port = ip_port_part.rsplit(':', 1)
            response_time = proxy_entry[0]
            return (int(port), response_time)
        except (ValueError, IndexError):
            # 如果解析失败，返回一个很大的端口和响应时间，确保排在最后
            return (65536, 99999)
    
    # 按端口和响应时间排序
    successful_proxies_sorted = sorted(successful_proxies, key=get_port_and_time)
    
    with open(SUCCESS_PROXY_FILE, 'w', encoding='utf-8') as f:
        for _, proxy in successful_proxies_sorted:
            f.write(f"{proxy}\n")
    print(f"提取了 {len(successful_proxies_sorted)} 个有效代理到 {SUCCESS_PROXY_FILE}")
except Exception as e:
    print(f"保存 {SUCCESS_PROXY_FILE} 时发生异常: {str(e)}")
    exit(1)

# 步骤7: 从成功代理中提取优选代理到 {base_name}_preferred.txt
try:
    # 清空优选代理文件
    with open(PREFERRED_PROXY_FILE, 'w', encoding='utf-8') as f:
        pass
    
    preferred_proxies = []
    preferred_port_proxies = []  # 根据端口筛选后的代理
    
    if os.path.exists(SUCCESS_PROXY_FILE):
        with open(SUCCESS_PROXY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    # 提取响应时间和端口
                    ip_port_part = line.split('#')[0]
                    _, port = ip_port_part.rsplit(':', 1)
                    response_time = int(line.split('#')[1].replace('ms', ''))
                    
                    # 如果响应时间小于设定阈值，则添加到优选列表
                    if response_time < PREFERRED_MAX_RESPONSE_TIME:
                        preferred_proxies.append((response_time, line, port))
                except (ValueError, IndexError):
                    print(f"无效行格式: {line}")
                    continue
    
    # 按端口（从小到大）和响应时间（从小到大）排序
    preferred_proxies.sort(key=lambda x: (int(x[2]), x[0]))
    
    # 如果设置了优选反代端口，进行端口筛选
    if PREFERRED_PROXY_PORT:
        # 处理多个端口的情况（用逗号分隔）
        preferred_ports = [p.strip() for p in PREFERRED_PROXY_PORT.split(',') if p.strip()]
        
        for response_time, proxy, port in preferred_proxies:
            if port in preferred_ports:
                preferred_port_proxies.append((response_time, proxy))
        
        # 按端口和响应时间排序
        preferred_port_proxies_sorted = sorted(preferred_port_proxies, key=lambda x: get_port_and_time(x))
        
        # 保存端口筛选后的优选代理
        if preferred_port_proxies_sorted:
            with open(PREFERRED_PROXY_FILE, 'w', encoding='utf-8') as f:
                for _, proxy in preferred_port_proxies_sorted:
                    f.write(f"{proxy}\n")
            print(f"提取了 {len(preferred_port_proxies_sorted)} 个响应时间小于{PREFERRED_MAX_RESPONSE_TIME}ms且端口为{PREFERRED_PROXY_PORT}的优选代理到 {PREFERRED_PROXY_FILE}")
        else:
            print(f"无响应时间小于{PREFERRED_MAX_RESPONSE_TIME}ms且端口为{PREFERRED_PROXY_PORT}的优选代理。")
    else:
        # 没有设置端口筛选，直接保存所有优选代理（已排序）
        if preferred_proxies:
            with open(PREFERRED_PROXY_FILE, 'w', encoding='utf-8') as f:
                for _, proxy, _ in preferred_proxies:
                    f.write(f"{proxy}\n")
            print(f"提取了 {len(preferred_proxies)} 个响应时间小于{PREFERRED_MAX_RESPONSE_TIME}ms的优选代理到 {PREFERRED_PROXY_FILE}")
        else:
            print(f"无响应时间小于{PREFERRED_MAX_RESPONSE_TIME}ms的优选代理。")
        
except Exception as e:
    print(f"提取优选代理时发生异常: {str(e)}")

# 美化输出：显示成功代理列表
print("\n" + "="*80)
print("代理检查完成！以下是成功代理：")
print("="*80)

# 显示优选代理（响应时间小于设定阈值）
if PREFERRED_PROXY_PORT and preferred_port_proxies:
    print(f"\n优选代理 (响应时间 < {PREFERRED_MAX_RESPONSE_TIME}ms 且端口为 {PREFERRED_PROXY_PORT}):")
    for _, proxy in preferred_port_proxies:
        parts = proxy.split('#')
        if len(parts) >= 2:
            ip_port = parts[0]
            status = parts[1]
            display_proxy = f"{ip_port} ({status})"
        else:
            display_proxy = proxy
        print(f"  ★ {display_proxy}")
elif not PREFERRED_PROXY_PORT and preferred_proxies:
    print(f"\n优选代理 (响应时间 < {PREFERRED_MAX_RESPONSE_TIME}ms):")
    for _, proxy, _ in preferred_proxies:
        parts = proxy.split('#')
        if len(parts) >= 2:
            ip_port = parts[0]
            status = parts[1]
            display_proxy = f"{ip_port} ({status})"
        else:
            display_proxy = proxy
        print(f"  ★ {display_proxy}")

# 显示所有成功代理
if successful_proxies:
    print(f"\n所有成功代理 (共 {len(successful_proxies)} 个):")
    for _, proxy in successful_proxies:
        parts = proxy.split('#')
        if len(parts) >= 2:
            ip_port = parts[0]
            status = parts[1]
            display_proxy = f"{ip_port} ({status})"
        else:
            display_proxy = proxy
        print(f"  ✓ {display_proxy}")

print("="*80)
print(f"总共保存 {len(successful_proxies)} 个成功代理到 {SUCCESS_PROXY_FILE}")

if PREFERRED_PROXY_PORT:
    print(f"优选代理 (响应时间 < {PREFERRED_MAX_RESPONSE_TIME}ms 且端口为 {PREFERRED_PROXY_PORT}): {len(preferred_port_proxies)} 个 -> {PREFERRED_PROXY_FILE}")
else:
    print(f"优选代理 (响应时间 < {PREFERRED_MAX_RESPONSE_TIME}ms): {len(preferred_proxies)} 个 -> {PREFERRED_PROXY_FILE}")

print(f"完整curl响应（所有代理）已保存到 {FULL_RESPONSES_FILE}")
print("="*80)