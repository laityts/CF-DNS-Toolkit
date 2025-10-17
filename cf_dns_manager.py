#!/usr/bin/env python3
"""
Cloudflare DNS记录交互式管理工具（专为三级域名优化）
功能：查询域名DNS记录、根据IP删除DNS记录、添加DNS记录
作者：根据用户需求编写
日期：2025-10-04
版本：v2.2
"""

import requests
import json
import os
import sys
import re
from typing import List, Dict, Any, Optional

class ConfigManager:
    """配置管理器，支持环境变量和配置文件"""
    
    def __init__(self):
        self.config_file = ".cloudflare_dnsm_config"
        
    def load_config(self) -> Dict[str, str]:
        """
        加载配置，优先级：环境变量 > 配置文件
        
        Returns:
            配置字典
        """
        config = {}
        
        # 从环境变量读取
        config['AUTH_EMAIL'] = os.getenv('CLOUDFLARE_AUTH_EMAIL', '')
        config['AUTH_KEY'] = os.getenv('CLOUDFLARE_AUTH_KEY', '')  # 全局API密钥
        
        # 如果环境变量没有完整配置，检查配置文件
        if not all([config['AUTH_EMAIL'], config['AUTH_KEY']]):
            file_config = self._load_config_file()
            if file_config:
                for key in ['AUTH_EMAIL', 'AUTH_KEY']:
                    if not config.get(key) and file_config.get(key):
                        config[key] = file_config[key]
        
        return config
    
    def _load_config_file(self) -> Dict[str, str]:
        """
        从配置文件读取配置
        
        Returns:
            配置字典，如果文件不存在返回空字典
        """
        if not os.path.exists(self.config_file):
            return {}
            
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = {}
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
                return config
        except Exception as e:
            print(f"❌ 读取配置文件失败: {e}")
            return {}
    
    def save_config(self, config: Dict[str, str]):
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                f.write("# Cloudflare DNS管理工具配置文件\n")
                f.write("# 请勿泄露此文件内容！\n\n")
                for key, value in config.items():
                    if value:  # 只保存非空值
                        f.write(f"{key}={value}\n")
            self.print_status(f"配置已保存到 {self.config_file}", "success")
        except Exception as e:
            self.print_status(f"保存配置失败: {e}", "error")
    
    def print_status(self, message: str, status: str = "info"):
        """打印状态消息"""
        icons = {
            "info": "📝",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌"
        }
        icon = icons.get(status, "📝")
        print(f"{icon} {message}")

class DNSManager:
    def __init__(self, auth_email: str, auth_key: str):
        """
        初始化Cloudflare DNS管理器
        
        Args:
            auth_email: Cloudflare账户邮箱
            auth_key: Cloudflare全局API密钥
        """
        self.auth_email = auth_email
        self.auth_key = auth_key
        self.base_url = "https://api.cloudflare.com/client/v4"
        
        # 设置请求头
        self.headers = {
            "X-Auth-Email": auth_email,
            "X-Auth-Key": auth_key,
            "Content-Type": "application/json"
        }
        
        # 缓存域名列表
        self._zones_cache = None
        
    def print_banner(self, title: str):
        """打印美观的标题横幅"""
        print("\n" + "=" * 60)
        print(f"✨ {title}")
        print("=" * 60)
        
    def print_section(self, title: str):
        """打印章节标题"""
        print(f"\n🎯 {title}")
        print("-" * 40)
    
    def print_status(self, message: str, status: str = "info"):
        """打印状态消息"""
        icons = {
            "info": "📝",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌"
        }
        icon = icons.get(status, "📝")
        print(f"{icon} {message}")
    
    def test_authentication(self) -> bool:
        """
        测试API认证是否有效
        
        Returns:
            bool: 认证是否成功
        """
        self.print_section("测试API认证")
        
        try:
            self.print_status("使用全局API密钥认证...")
            # 测试获取用户信息
            url = f"{self.base_url}/user"
            
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get("success", False):
                user_email = result.get('result', {}).get('email', '未知')
                self.print_status(f"全局API密钥认证成功，用户: {user_email}", "success")
                return True
            else:
                errors = result.get('errors', [{'message': '未知错误'}])
                error_msg = errors[0].get('message', '未知错误') if errors else '未知错误'
                self.print_status(f"认证失败: {error_msg}", "error")
                return False
                
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    errors = error_detail.get('errors', [])
                    if errors:
                        error_msg = errors[0].get('message', '未知错误')
                        self.print_status(f"API请求失败: {e.response.status_code} - {error_msg}", "error")
                    else:
                        self.print_status(f"API请求失败: {e.response.status_code} - {e.response.text}", "error")
                except:
                    self.print_status(f"API请求失败: {e.response.status_code} - {e.response.text}", "error")
            else:
                self.print_status(f"网络错误: {str(e)}", "error")
            return False
        except Exception as e:
            self.print_status(f"未知错误: {str(e)}", "error")
            return False
    
    def get_all_zones(self) -> List[Dict[str, Any]]:
        """
        获取账户下的所有域名（Zones）
        
        Returns:
            域名列表
        """
        if self._zones_cache:
            return self._zones_cache
            
        self.print_section("获取域名列表")
        
        # 先测试认证
        if not self.test_authentication():
            self.print_status("认证失败，无法获取域名列表", "error")
            return []
        
        try:
            url = f"{self.base_url}/zones"
            params = {"per_page": 100, "page": 1}  # 每页最多100个域名
            
            all_zones = []
            while True:
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
                response.raise_for_status()
                
                result = response.json()
                
                if not result.get("success", False):
                    error_msg = result.get('errors', [{'message': '未知错误'}])[0].get('message', '未知错误')
                    self.print_status(f"获取域名列表失败: {error_msg}", "error")
                    break
                    
                zones = result.get("result", [])
                all_zones.extend(zones)
                
                # 检查是否有更多页面
                result_info = result.get('result_info', {})
                if result_info.get('page', 0) >= result_info.get('total_pages', 1):
                    break
                    
                params['page'] += 1
            
            self._zones_cache = all_zones
            self.print_status(f"找到 {len(all_zones)} 个域名", "success")
            return all_zones
            
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    errors = error_detail.get('errors', [])
                    if errors:
                        error_msg = errors[0].get('message', '未知错误')
                        self.print_status(f"获取域名列表失败: {e.response.status_code} - {error_msg}", "error")
                    else:
                        self.print_status(f"获取域名列表失败: {e.response.status_code} - {e.response.text}", "error")
                except:
                    self.print_status(f"获取域名列表失败: {e.response.status_code} - {e.response.text}", "error")
            else:
                self.print_status(f"网络错误: {str(e)}", "error")
            return []
        except Exception as e:
            self.print_status(f"未知错误: {str(e)}", "error")
            return []
    
    def select_zone_interactive(self) -> Optional[Dict[str, Any]]:
        """
        交互式选择域名
        
        Returns:
            选择的域名信息，如果取消返回None
        """
        zones = self.get_all_zones()
        if not zones:
            self.print_status("没有找到任何域名", "error")
            return None
        
        print("\n📋 可用域名列表:")
        print("-" * 80)
        print(f"{'序号':<4} {'域名':<30} {'状态':<10} {'ID':<30}")
        print("-" * 80)
        
        for i, zone in enumerate(zones, 1):
            zone_name = zone.get('name', 'N/A')
            zone_status = zone.get('status', 'N/A')
            zone_id = zone.get('id', 'N/A')
            
            # 截断过长的内容以便显示
            if len(zone_name) > 28:
                zone_name = zone_name[:25] + "..."
            if len(zone_id) > 27:
                zone_id = zone_id[:24] + "..."
            
            print(f"{i:<4} {zone_name:<30} {zone_status:<10} {zone_id:<30}")
        
        print("-" * 80)
        
        while True:
            try:
                choice = input(f"\n请选择域名 (1-{len(zones)}, 输入q退出): ").strip()
                if choice.lower() == 'q':
                    return None
                
                index = int(choice) - 1
                if 0 <= index < len(zones):
                    selected_zone = zones[index]
                    self.print_status(f"已选择域名: {selected_zone.get('name')}", "success")
                    return selected_zone
                else:
                    self.print_status(f"请输入 1-{len(zones)} 之间的数字", "warning")
            except ValueError:
                self.print_status("请输入有效的数字", "warning")
    
    def get_dns_records(self, zone_id: str, domain: str = None) -> List[Dict[str, Any]]:
        """
        获取指定域名的DNS记录
        
        Args:
            zone_id: 域名区域ID
            domain: 具体域名（可选）
            
        Returns:
            DNS记录列表
        """
        self.print_section("获取DNS记录")
        
        try:
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            params = {}
            if domain:
                params["name"] = domain
                self.print_status(f"正在查询域名 {domain} 的DNS记录...")
            else:
                self.print_status("正在查询所有DNS记录...")
            
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if not result.get("success", False):
                error_msg = result.get('errors', [{'message': '未知错误'}])[0].get('message', '未知错误')
                self.print_status(f"获取DNS记录失败: {error_msg}", "error")
                return []
                
            records = result.get("result", [])
            
            # 为每条记录添加zone_id信息
            for record in records:
                record['zone_id'] = zone_id
            
            if domain:
                # 过滤指定域名的记录
                filtered_records = [record for record in records if record.get('name') == domain]
                self.print_status(f"找到 {len(filtered_records)} 条域名 {domain} 的DNS记录", "success")
                return filtered_records
            else:
                self.print_status(f"找到 {len(records)} 条DNS记录", "success")
                return records
            
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    errors = error_detail.get('errors', [])
                    if errors:
                        error_msg = errors[0].get('message', '未知错误')
                        self.print_status(f"获取DNS记录失败: {e.response.status_code} - {error_msg}", "error")
                    else:
                        self.print_status(f"获取DNS记录失败: {e.response.status_code} - {e.response.text}", "error")
                except:
                    self.print_status(f"获取DNS记录失败: {e.response.status_code} - {e.response.text}", "error")
            else:
                self.print_status(f"网络错误: {str(e)}", "error")
            return []
        except Exception as e:
            self.print_status(f"未知错误: {str(e)}", "error")
            return []
    
    def get_all_dns_records_across_zones(self, target_domain: str = None) -> List[Dict[str, Any]]:
        """
        获取所有域名下的DNS记录（跨域名查询）
        
        Args:
            target_domain: 目标域名（可选）
            
        Returns:
            DNS记录列表，包含域名信息
        """
        self.print_section("跨域名查询DNS记录")
        
        zones = self.get_all_zones()
        if not zones:
            return []
        
        all_records = []
        
        for zone in zones:
            zone_name = zone.get('name', 'Unknown')
            zone_id = zone.get('id')
            
            self.print_status(f"正在查询域名 {zone_name}...")
            records = self.get_dns_records(zone_id, target_domain)
            
            # 为每条记录添加域名信息
            for record in records:
                record['zone_name'] = zone_name
                record['zone_id'] = zone_id
            
            all_records.extend(records)
            
            # 短暂延迟避免API限制
            import time
            time.sleep(0.2)
        
        if target_domain:
            self.print_status(f"在所有域名中找到 {len(all_records)} 条关于 {target_domain} 的DNS记录", "success")
        else:
            self.print_status(f"在所有域名中找到 {len(all_records)} 条DNS记录", "success")
        
        return all_records
    
    def display_records_table(self, records: List[Dict[str, Any]]):
        """
        以表格形式显示DNS记录
        
        Args:
            records: DNS记录列表
        """
        if not records:
            self.print_status("没有找到DNS记录", "warning")
            return
        
        print("\n📋 DNS记录列表:")
        print("-" * 130)
        print(f"{'序号':<4} {'域名':<20} {'类型':<6} {'名称':<25} {'内容':<20} {'TTL':<6} {'ID':<20}")
        print("-" * 130)
        
        for i, record in enumerate(records, 1):
            zone_name = record.get('zone_name', 'N/A')
            record_type = record.get('type', 'N/A')
            record_name = record.get('name', 'N/A')
            record_content = record.get('content', 'N/A')
            record_ttl = record.get('ttl', 'N/A')
            record_id = record.get('id', 'N/A')
            
            # 截断过长的内容以便显示
            if len(zone_name) > 18:
                zone_name = zone_name[:15] + "..."
            if len(record_name) > 23:
                record_name = record_name[:20] + "..."
            if len(record_content) > 18:
                record_content = record_content[:15] + "..."
            if len(record_id) > 17:
                record_id = record_id[:14] + "..."
            
            print(f"{i:<4} {zone_name:<20} {record_type:<6} {record_name:<25} {record_content:<20} {record_ttl:<6} {record_id:<20}")
        
        print("-" * 130)
    
    def search_dns_records_by_subdomain(self, zone_id: str, subdomain_pattern: str) -> List[Dict[str, Any]]:
        """
        根据子域名模式搜索DNS记录
        
        Args:
            zone_id: 域名区域ID
            subdomain_pattern: 子域名模式（如 se.proxyip）
            
        Returns:
            匹配的DNS记录列表
        """
        self.print_section(f"搜索子域名包含 '{subdomain_pattern}' 的DNS记录")
        
        all_records = self.get_dns_records(zone_id)
        if not all_records:
            return []
        
        matching_records = []
        for record in all_records:
            record_name = record.get('name', '')
            if subdomain_pattern in record_name:
                # 确保记录包含zone_id信息
                record['zone_id'] = zone_id
                matching_records.append(record)
        
        self.print_status(f"找到 {len(matching_records)} 条包含 '{subdomain_pattern}' 的DNS记录", "success")
        return matching_records
    
    def delete_dns_record_by_ip(self, ip: str, target_domain: str = None) -> int:
        """
        根据IP地址删除DNS记录（跨域名）
        
        Args:
            ip: 要删除的IP地址
            target_domain: 限制删除的域名（可选）
            
        Returns:
            删除的记录数量
        """
        self.print_section(f"删除IP为 {ip} 的DNS记录（所有域名）")
        
        # 获取所有记录
        all_records = self.get_all_dns_records_across_zones(target_domain)
        if not all_records:
            return 0
        
        # 筛选匹配IP的记录
        matching_records = []
        for record in all_records:
            if (record.get('type') in ['A', 'AAAA'] and 
                record.get('content') == ip and
                (target_domain is None or record.get('name') == target_domain)):
                matching_records.append(record)
        
        if not matching_records:
            self.print_status(f"没有找到IP为 {ip} 的DNS记录", "warning")
            return 0
        
        # 显示匹配的记录
        print(f"\n🔍 找到 {len(matching_records)} 条IP为 {ip} 的DNS记录:")
        self.display_records_table(matching_records)
        
        # 确认删除
        confirm = input(f"\n⚠️  确定要删除这 {len(matching_records)} 条记录吗？(y/N): ").strip().lower()
        if confirm != 'y':
            self.print_status("取消删除操作", "info")
            return 0
        
        # 执行删除
        deleted_count = 0
        for record in matching_records:
            zone_id = record.get('zone_id')
            record_id = record.get('id')
            record_name = record.get('name')
            record_content = record.get('content')
            
            if self._delete_single_record(zone_id, record_id, record_content):
                deleted_count += 1
                self.print_status(f"已删除记录: {record_name} -> {record_content}", "success")
            else:
                self.print_status(f"删除记录失败: {record_name} -> {record_content}", "error")
            
            # 短暂延迟避免API限制
            import time
            time.sleep(0.5)
        
        self.print_status(f"删除完成，共删除 {deleted_count} 条记录", "success")
        return deleted_count
    
    def delete_dns_records_by_subdomain(self, subdomain_pattern: str, ip: str = None) -> int:
        """
        根据子域名模式删除DNS记录
        
        Args:
            subdomain_pattern: 子域名模式（如 se.proxyip）
            ip: 限制删除的IP地址（可选）
            
        Returns:
            删除的记录数量
        """
        self.print_section(f"删除子域名包含 '{subdomain_pattern}' 的DNS记录")
        
        # 搜索匹配的记录
        matching_records = self.search_dns_records_by_subdomain(subdomain_pattern)
        if not matching_records:
            return 0
        
        # 如果指定了IP，进一步筛选
        if ip:
            filtered_records = [record for record in matching_records if record.get('content') == ip]
            if not filtered_records:
                self.print_status(f"没有找到子域名包含 '{subdomain_pattern}' 且IP为 {ip} 的DNS记录", "warning")
                return 0
            matching_records = filtered_records
            self.print_status(f"找到 {len(matching_records)} 条子域名包含 '{subdomain_pattern}' 且IP为 {ip} 的DNS记录", "success")
        else:
            self.print_status(f"找到 {len(matching_records)} 条子域名包含 '{subdomain_pattern}' 的DNS记录", "success")
        
        # 显示匹配的记录
        self.display_records_table(matching_records)
        
        # 确认删除
        confirm = input(f"\n⚠️  确定要删除这 {len(matching_records)} 条记录吗？(y/N): ").strip().lower()
        if confirm != 'y':
            self.print_status("取消删除操作", "info")
            return 0
        
        # 执行删除
        deleted_count = 0
        for record in matching_records:
            zone_id = record.get('zone_id')
            record_id = record.get('id')
            record_name = record.get('name')
            record_content = record.get('content')
            
            if self._delete_single_record(zone_id, record_id, record_content):
                deleted_count += 1
                self.print_status(f"已删除记录: {record_name} -> {record_content}", "success")
            else:
                self.print_status(f"删除记录失败: {record_name} -> {record_content}", "error")
            
            # 短暂延迟避免API限制
            import time
            time.sleep(0.5)
        
        self.print_status(f"删除完成，共删除 {deleted_count} 条记录", "success")
        return deleted_count
    
    def _delete_single_record(self, zone_id: str, record_id: str, ip: str) -> bool:
        """
        删除单个DNS记录
        
        Args:
            zone_id: 域名区域ID
            record_id: 要删除的记录ID
            ip: 对应的IP地址(用于日志)
            
        Returns:
            bool: 删除是否成功
        """
        try:
            url = f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}"
            
            response = requests.delete(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get("success", False):
                return True
            else:
                errors = result.get('errors', [{'message': '未知错误'}])
                error_msg = errors[0].get('message', '未知错误') if errors else '未知错误'
                self.print_status(f"删除DNS记录失败: {error_msg}", "error")
                return False
                
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    errors = error_detail.get('errors', [])
                    if errors:
                        error_msg = errors[0].get('message', '未知错误')
                        self.print_status(f"删除DNS记录失败: {e.response.status_code} - {error_msg}", "error")
                    else:
                        self.print_status(f"删除DNS记录失败: {e.response.status_code} - {e.response.text}", "error")
                except:
                    self.print_status(f"删除DNS记录失败: {e.response.status_code} - {e.response.text}", "error")
            else:
                self.print_status(f"删除DNS记录时发生网络错误: {str(e)}", "error")
            return False
        except Exception as e:
            self.print_status(f"删除DNS记录时发生未知错误: {str(e)}", "error")
            return False

    def delete_selected_record(self, records: List[Dict[str, Any]]) -> bool:
        """
        删除用户选择的DNS记录
        
        Args:
            records: DNS记录列表
            
        Returns:
            bool: 是否继续操作
        """
        if not records:
            return True
            
        while True:
            try:
                choice = input(f"\n请输入要删除的记录序号 (1-{len(records)}，输入0返回): ").strip()
                if choice == '0':
                    return True
                
                index = int(choice) - 1
                if 0 <= index < len(records):
                    record = records[index]
                    zone_id = record.get('zone_id')
                    record_id = record.get('id')
                    record_name = record.get('name')
                    record_content = record.get('content')
                    
                    # 确认删除
                    confirm = input(f"⚠️  确定要删除记录 {record_name} -> {record_content} 吗？(y/N): ").strip().lower()
                    if confirm == 'y':
                        if self._delete_single_record(zone_id, record_id, record_content):
                            self.print_status(f"已删除记录: {record_name} -> {record_content}", "success")
                            
                            # 从列表中移除已删除的记录
                            records.pop(index)
                            
                            # 如果还有记录，重新显示
                            if records:
                                self.display_records_table(records)
                            else:
                                self.print_status("所有记录已删除", "success")
                                return True
                        else:
                            self.print_status(f"删除记录失败: {record_name} -> {record_content}", "error")
                    else:
                        self.print_status("取消删除", "info")
                else:
                    self.print_status(f"请输入 0-{len(records)} 之间的数字", "warning")
                    
                # 询问是否继续删除
                continue_delete = input("\n是否继续删除其他记录？(y/N): ").strip().lower()
                if continue_delete != 'y':
                    return True
                    
            except ValueError:
                self.print_status("请输入有效的数字", "warning")

    def add_dns_record(self, zone_id: str, domain: str, ip: str, record_type: str = "A", ttl: int = 1, proxied: bool = False) -> bool:
        """
        添加DNS记录
        
        Args:
            zone_id: 域名区域ID
            domain: 域名
            ip: IP地址
            record_type: 记录类型，默认为A记录
            ttl: TTL值，默认为1（自动）
            proxied: 是否通过Cloudflare代理，默认为False
            
        Returns:
            bool: 添加是否成功
        """
        self.print_section("添加DNS记录")
        self.print_status(f"正在添加记录: {domain} -> {ip} (类型: {record_type})")
        
        # 验证IP地址格式
        if record_type == "A" and not self._is_valid_ipv4(ip):
            self.print_status(f"IPv4地址格式无效: {ip}", "error")
            return False
        elif record_type == "AAAA" and not self._is_valid_ipv6(ip):
            self.print_status(f"IPv6地址格式无效: {ip}", "error")
            return False
        
        try:
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            
            data = {
                "type": record_type,
                "name": domain,
                "content": ip,
                "ttl": ttl,
                "proxied": proxied
            }
            
            response = requests.post(url, headers=self.headers, data=json.dumps(data), timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get("success", False):
                record_id = result.get('result', {}).get('id', '未知')
                self.print_status(f"成功创建DNS记录: {domain} -> {ip}", "success")
                return True
            else:
                errors = result.get('errors', [{'message': '未知错误'}])
                error_msg = errors[0].get('message', '未知错误') if errors else '未知错误'
                self.print_status(f"创建DNS记录失败: {error_msg}", "error")
                return False
                
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    errors = error_detail.get('errors', [])
                    if errors:
                        error_msg = errors[0].get('message', '未知错误')
                        self.print_status(f"创建DNS记录失败: {e.response.status_code} - {error_msg}", "error")
                    else:
                        self.print_status(f"创建DNS记录失败: {e.response.status_code} - {e.response.text}", "error")
                except:
                    self.print_status(f"创建DNS记录失败: {e.response.status_code} - {e.response.text}", "error")
            else:
                self.print_status(f"创建DNS记录时发生网络错误: {str(e)}", "error")
            return False
        except Exception as e:
            self.print_status(f"创建DNS记录时发生未知错误: {str(e)}", "error")
            return False
    
    def _is_valid_ipv4(self, ip: str) -> bool:
        """验证IPv4地址格式"""
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if not re.match(ip_pattern, ip):
            return False
        
        parts = ip.split('.')
        for part in parts:
            if not part.isdigit() or not 0 <= int(part) <= 255:
                return False
        return True
    
    def _is_valid_ipv6(self, ip: str) -> bool:
        """简单验证IPv6地址格式"""
        ipv6_pattern = r'^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|^::1$|^::$'
        return bool(re.match(ipv6_pattern, ip))

def clear_screen():
    """清空终端屏幕"""
    os.system('cls' if os.name == 'nt' else 'clear')

def print_menu():
    """打印主菜单"""
    print("\n" + "=" * 60)
    print("🌐 Cloudflare DNS记录管理工具 v2.2（三级域名优化）")
    print("=" * 60)
    print("1. 📋 查询域名DNS记录")
    print("2. ➕ 添加DNS记录")
    print("3. 🗑️  根据IP删除DNS记录（所有域名）")
    print("4. ⚙️  配置认证信息")
    print("5. 🚪 退出")
    print("=" * 60)

def print_zone_submenu():
    """打印域名子菜单"""
    print("\n" + "-" * 50)
    print("📋 域名DNS记录操作")
    print("-" * 50)
    print("1. 🔍 查询子域名DNS记录")
    print("2. 🌐 查询该域名所有DNS记录")
    print("3. ↩️  返回主菜单")
    print("-" * 50)

def setup_authentication():
    """交互式设置认证信息"""
    clear_screen()
    print("🔐 Cloudflare API认证设置")
    print("=" * 50)
    
    config = {}
    
    print("\n使用全局API密钥认证:")
    print("1. 登录Cloudflare控制台")
    print("2. 进入「我的个人资料」->「API令牌」")
    print("3. 在「API密钥」部分查看「全局API密钥」")
    
    auth_email = input("\n请输入账户邮箱: ").strip()
    if not auth_email:
        print("❌ 邮箱不能为空")
        return None
        
    auth_key = input("请输入全局API密钥: ").strip()
    if not auth_key:
        print("❌ 全局API密钥不能为空")
        return None
        
    config['AUTH_EMAIL'] = auth_email
    config['AUTH_KEY'] = auth_key
    
    return config

def main():
    """
    主函数 - 交互式DNS记录管理
    """
    clear_screen()
    
    # 加载配置
    config_manager = ConfigManager()
    config = config_manager.load_config()
    
    # 检查必要配置
    required_auth = config.get('AUTH_EMAIL') and config.get('AUTH_KEY')
    
    if not required_auth:
        print("❌ 未找到有效的认证配置")
        print("\n请先设置Cloudflare API认证信息")
        
        new_config = setup_authentication()
        if new_config:
            config_manager.save_config(new_config)
            config = new_config
        else:
            print("❌ 认证设置失败，程序退出")
            return
    
    # 创建管理器实例
    manager = DNSManager(
        auth_email=config.get('AUTH_EMAIL', ''),
        auth_key=config.get('AUTH_KEY', '')
    )
    
    # 测试认证
    if not manager.test_authentication():
        print("\n❌ 认证失败，请检查配置")
        retry = input("是否重新配置认证信息？(y/N): ").strip().lower()
        if retry == 'y':
            new_config = setup_authentication()
            if new_config:
                config_manager.save_config(new_config)
                config = new_config
                manager = DNSManager(
                    auth_email=config.get('AUTH_EMAIL', ''),
                    auth_key=config.get('AUTH_KEY', '')
                )
            else:
                print("❌ 认证设置失败，程序退出")
                return
        else:
            print("❌ 认证失败，程序退出")
            return
    
    while True:
        print_menu()
        choice = input("\n请选择操作 (1-5): ").strip()
        
        if choice == '1':
            while True:
                clear_screen()
                manager.print_banner("查询域名DNS记录")
                
                zone_info = manager.select_zone_interactive()
                if not zone_info:
                    break
                    
                zone_id = zone_info.get('id')
                zone_name = zone_info.get('name')
                
                while True:
                    clear_screen()
                    manager.print_banner(f"域名: {zone_name}")
                    print_zone_submenu()
                    
                    sub_choice = input("\n请选择操作 (1-3): ").strip()
                    
                    if sub_choice == '1':
                        clear_screen()
                        manager.print_banner(f"查询子域名DNS记录 - {zone_name}")
                        
                        subdomain_pattern = input("请输入要查询的子域名模式 (如 se.proxyip): ").strip()
                        if not subdomain_pattern:
                            manager.print_status("子域名模式不能为空", "error")
                            input("\n按回车键继续...")
                            continue
                            
                        records = manager.search_dns_records_by_subdomain(zone_id, subdomain_pattern)
                        manager.display_records_table(records)
                        
                        if records:
                            # 询问是否删除记录
                            delete_choice = input("\n是否要删除记录？(y/N): ").strip().lower()
                            if delete_choice == 'y':
                                if not manager.delete_selected_record(records):
                                    break
                        
                        # 询问是否继续
                        continue_ops = input("\n是否继续查询其他子域名？(y/N): ").strip().lower()
                        if continue_ops != 'y':
                            break
                            
                    elif sub_choice == '2':
                        clear_screen()
                        manager.print_banner(f"查询所有DNS记录 - {zone_name}")
                        
                        records = manager.get_dns_records(zone_id)
                        manager.display_records_table(records)
                        
                        if records:
                            # 询问是否删除记录
                            delete_choice = input("\n是否要删除记录？(y/N): ").strip().lower()
                            if delete_choice == 'y':
                                if not manager.delete_selected_record(records):
                                    break
                        
                        # 询问是否继续
                        continue_ops = input("\n按回车键返回子菜单...")
                        break
                            
                    elif sub_choice == '3':
                        break
                    else:
                        manager.print_status("无效选择，请重新输入", "error")
                        input("\n按回车键继续...")
                
                # 询问是否继续查询其他域名
                continue_zone = input("\n是否继续查询其他域名？(y/N): ").strip().lower()
                if continue_zone != 'y':
                    break
            
            clear_screen()
            
        elif choice == '2':
            while True:
                clear_screen()
                manager.print_banner("添加DNS记录")
                
                # 选择域名
                zone_info = manager.select_zone_interactive()
                if not zone_info:
                    break
                    
                zone_id = zone_info.get('id')
                zone_name = zone_info.get('name')
                
                # 初始化子域名变量
                current_subdomain = ""
                
                while True:
                    clear_screen()
                    manager.print_banner(f"添加DNS记录 - 域名: {zone_name}")
                    
                    # 如果已有子域名，显示当前子域名状态
                    if current_subdomain:
                        print(f"💡 当前子域名: {current_subdomain}")
                        print("💡 直接回车使用当前子域名，或输入新的子域名")
                        print("-" * 50)
                    
                    # 获取子域名输入
                    subdomain_input = input(f"请输入子域名 (如 se.proxyip): ").strip()
                    
                    # 处理子域名输入
                    if subdomain_input:
                        # 用户输入了新的子域名
                        current_subdomain = subdomain_input
                    elif not current_subdomain:
                        # 第一次循环且没有输入子域名
                        manager.print_status("子域名不能为空", "error")
                        input("\n按回车键继续...")
                        continue
                    # 如果用户直接回车且有current_subdomain，则使用当前的子域名
                    
                    # 构建完整域名
                    if current_subdomain:
                        full_domain = f"{current_subdomain}.{zone_name}"
                    else:
                        full_domain = zone_name
                    
                    # 获取IP地址
                    ip = input("请输入IP地址: ").strip()
                    if not ip:
                        manager.print_status("IP地址不能为空", "error")
                        input("\n按回车键继续...")
                        continue
                    
                    # 选择记录类型
                    record_type = input("请输入记录类型 (默认: A): ").strip().upper()
                    if not record_type:
                        record_type = "A"
                    
                    # 选择TTL
                    ttl_input = input("请输入TTL值 (默认: 1-自动): ").strip()
                    if ttl_input:
                        try:
                            ttl = int(ttl_input)
                        except ValueError:
                            manager.print_status("TTL必须是数字，使用默认值1", "warning")
                            ttl = 1
                    else:
                        ttl = 1
                    
                    # 选择代理状态
                    proxied_input = input("是否通过Cloudflare代理？(y/N): ").strip().lower()
                    proxied = proxied_input in ['y', 'yes']
                    
                    # 确认添加
                    print(f"\n📋 将要添加的记录:")
                    print(f"   完整域名: {full_domain}")
                    print(f"   IP地址: {ip}")
                    print(f"   记录类型: {record_type}")
                    print(f"   TTL: {ttl}")
                    print(f"   代理状态: {'是' if proxied else '否'}")
                    
                    confirm = input("\n确认添加此记录？(y/N): ").strip().lower()
                    if confirm != 'y':
                        manager.print_status("取消添加操作", "info")
                        # 不退出循环，允许用户继续添加其他记录
                        continue_choice = input("\n是否使用其他子域名继续添加？(y/N): ").strip().lower()
                        if continue_choice != 'y':
                            break
                        else:
                            current_subdomain = ""  # 重置子域名
                            continue
                    
                    # 执行添加
                    success = manager.add_dns_record(zone_id, full_domain, ip, record_type, ttl, proxied)
                    
                    if success:
                        manager.print_status("记录添加成功", "success")
                    else:
                        manager.print_status("记录添加失败", "error")
                    
                    # 询问是否继续添加
                    while True:
                        continue_choice = input("\n是否继续添加DNS记录？(y-继续/n-更换域名/q-返回主菜单): ").strip().lower()
                        
                        if continue_choice == 'y':
                            # 继续使用当前子域名添加
                            break
                        elif continue_choice == 'n':
                            # 更换子域名
                            current_subdomain = ""
                            break
                        elif continue_choice == 'q':
                            # 返回主菜单
                            break
                        else:
                            manager.print_status("请输入 y/n/q", "warning")
                    
                    if continue_choice == 'q':
                        break
                
                # 询问是否继续添加其他域名的记录
                if continue_choice == 'q':
                    break
                    
                continue_main = input("\n是否继续为其他域名添加DNS记录？(y/N): ").strip().lower()
                if continue_main != 'y':
                    break
            
            clear_screen()

        elif choice == '3':
            clear_screen()
            manager.print_banner("根据IP删除DNS记录（所有域名）")
            
            ip = input("请输入要删除的IP地址: ").strip()
            if not ip:
                manager.print_status("IP地址不能为空", "error")
                input("\n按回车键继续...")
                clear_screen()
                continue
            
            target_domain = input("是否限制在特定域名中删除？(输入完整域名或留空删除所有): ").strip()
            target_domain = target_domain if target_domain else None
            
            manager.delete_dns_record_by_ip(ip, target_domain)
            
            input("\n按回车键继续...")
            clear_screen()
            
        elif choice == '4':
            clear_screen()
            new_config = setup_authentication()
            if new_config:
                config_manager.save_config(new_config)
                config = new_config
                manager = DNSManager(
                    auth_email=config.get('AUTH_EMAIL', ''),
                    auth_key=config.get('AUTH_KEY', '')
                )
                # 测试新认证
                manager.test_authentication()
            input("\n按回车键继续...")
            clear_screen()
            
        elif choice == '5':
            print("\n👋 感谢使用，再见！")
            break
            
        else:
            print("\n❌ 无效选择，请重新输入")
            input("\n按回车键继续...")
            clear_screen()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  用户中断执行")
        sys.exit(0)
    except Exception as e:
        print(f"\n💥 程序执行时发生错误: {str(e)}")
        sys.exit(1)