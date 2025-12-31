import requests
import json
import os
import time
import streamlit as st

def get_github_token():
    """从Streamlit secrets获取GitHub Token"""
    try:
        return st.secrets["github"]["token"]
    except (KeyError, AttributeError):
        print("GitHub Token未在secrets.toml中配置")
        return None

def create_gist(file_name, content, description="Stock data cache", public=False):
    """创建一个新的GitHub Gist"""
    token = get_github_token()
    if not token:
        return None
    
    url = "https://api.github.com/gists"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    payload = {
        "description": description,
        "public": public,
        "files": {
            file_name: {
                "content": content
            }
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"创建Gist失败: {e}")
        return None

def update_gist(gist_id, file_name, content):
    """更新现有的GitHub Gist"""
    token = get_github_token()
    if not token:
        return None
    
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    payload = {
        "files": {
            file_name: {
                "content": content
            }
        }
    }
    
    try:
        response = requests.patch(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"更新Gist失败: {e}")
        return None

def get_gist(gist_id):
    """获取GitHub Gist内容"""
    token = get_github_token()
    if not token:
        return None
    
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"获取Gist失败: {e}")
        return None

def list_gists():
    """列出用户的所有Gist"""
    token = get_github_token()
    if not token:
        return []
    
    url = "https://api.github.com/gists"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"获取Gist列表失败: {e}")
        return []

def find_gist_by_description(description):
    """通过描述查找Gist"""
    gists = list_gists()
    if not gists:
        return None
    
    for gist in gists:
        if gist.get("description") == description:
            return gist
    
    return None

def gist_exists(gist_id):
    """检查Gist是否存在"""
    return get_gist(gist_id) is not None