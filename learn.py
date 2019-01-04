#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os, re, sys, bs4
import urllib
import time
import threading
import codecs
import getpass
import http.cookiejar
from bs4 import BeautifulSoup as bs

url = 'https://learn.tsinghua.edu.cn/'
user_agent = r'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/44.0.2403.157 Safari/537.36'
headers = { 'User-Agent': user_agent, 'Connection': 'keep-alive' }

cookie = http.cookiejar.MozillaCookieJar()
handler = urllib.request.HTTPCookieProcessor(cookie)
opener = urllib.request.build_opener(handler)

num_concurrent_connection = 8   # not used
chunk_size = 2097152    # 2MB # not used
simple_download_threshold = 39321600    # 37.5 MB, 5 min @ 1Mbps
large_file_list = 'large_download.txt'
failed_file_list = 'failed_download.txt'

pool_lock = threading.Lock()

class jobPool:
    def __init__(self, num):
        # 0 - not started
        # 1 - working
        # 2 - finished
        self.pool = [0] * num
        
        self.next = 0
        self.total = num
    
    def queryNext(self):
        if(self.next < self.total):
            self.pool[self.next] = 1    # working
            self.next += 1
            return self.next - 1
        else:
            return None


class Worker(threading.Thread):
    def __init__(self, id, refStatus, url, masterFileame, callback = None):
        self.callback = callback if callback else lambda: None
        self.threadID = id
        self.jobs = refStatus
        self.url = url
        self.master = masterFileame

    def run(self):
        with pool_lock:
            job = self.jobs.queryNext()
        if not job:
            return
        rangeDownload(self.url, self.master + '.part' + str(job + 1), job * chunk_size, (job+1) * chunk_size - 1)

def open_persistent(req):
    succ = False
    while not succ:
        try:
            response = opener.open(req)
            succ = True
        except urllib.error.URLError as e:
            print(e)
    return response

def querySize(url):
    request = build_request(url)
    request.method = 'HEAD'
    response = open_persistent(request)
    length = 0
    try:
        length = int(response.info()['Content-Length'])
        print("Response length: " + str(length))
    except TypeError:
        print("Response length cannot be determined")
    return length

def rangeDownload(url, filename, start, end):
    # This function does not work as expected
    request = build_request(url)
    request.add_header('Range', 'bytes=%s-%s' % (start, end))
    print(request.headers)

    response = open_persistent(request)
    status_code = response.getcode()
    length = int(response.info()['Content-Length'])
    print("Response length: " + str(length))
    if status_code >= 400:
        pass
    else:
        with open(filename, 'wb') as f:
            f.write(response.read())

def fastDownload(url, filename):
    # This function does not work as expected
    pass

def recordLargeDownload(url, path, size):
    with open(large_file_list, 'w+') as f:
        f.write(url + '\n')
        f.write(path + '\n')
        f.write(str(size) + '\n')

def recordFailedDownload(url, path):
    with open(failed_file_list, 'w+') as f:
        f.write(url + '\n')
        f.write(path + '\n')

def simpleDownload(url, path):
    succ = False
    while not succ:
        try:
            with open(path, 'wb') as f:
                f.write(open_page(url).read())
            succ = True
        except ConnectionError as e:
            print(e)
        except OSError as e:
            print(e)
            print('An error has occurred when downloading the file. Aborting.')
            try:
                recordFailedDownload(url, path)
            except Exception as e:
                print(e)
            succ = True

def download(url, path):
    size = querySize(url)
    if size <= simple_download_threshold:
        simpleDownload(url, path)
    else:
        print("File " + path +" is too large (" + str(size) +" bytes). Mark for delayed downloading.")
        recordLargeDownload(url, path, size)

def NTFSSan(s):
    s = s.replace(u'\xa0', u' ')
    s = s.rstrip(u' ')
    p = re.compile(r'[/:\*<>\"\?\|]')
    return p.sub("_", s)

def urlEncodeNonAscii(b):
    newb = bytearray()
    for single_byte in bytearray(b):
        if single_byte > 127:
            newb += b'%'
            num = '%02x' % single_byte
            newb += num.encode('ascii')
        else:
            newb.append(single_byte)
    return newb.decode('ascii')

def iriToUri(iri):
    parts= urllib.parse.urlparse(iri)
    return urllib.parse.urlunparse([urlEncodeNonAscii(part.encode('utf-8')) for part in parts])

def build_request(uri, values = {}):
    post_data = urllib.parse.urlencode(values).encode()
    if not uri.startswith("http"):
        uri = url + uri
    request = urllib.request.Request(uri, data=post_data, headers=headers)
    return request

def open_page(uri, values = {}):
    request = build_request(uri, values)
    #print(uri)
    response = open_persistent(request)
    return response

def get_page(uri, values = {}):
    data = open_page(uri, values)
    if data:
        succ = False
        while not succ:
            try:
                res = data.read().decode()
                succ = True
            except ConnectionError as e:
                print(e)
        return res

def login(username, password):
    login_uri = 'MultiLanguage/lesson/teacher/loginteacher.jsp'
    values = { 'userid': username, 'userpass': password, 'submit1': '登陆' }
    successful = get_page(login_uri, values).find('loginteacher_action.jsp') != -1
    print('Login successfully' if successful else 'Login failed!')
    return successful

def get_courses(typepage = 1):
    soup = bs(get_page('MultiLanguage/lesson/student/MyCourse.jsp?language=cn&typepage=' + str(typepage)), 'html.parser')
    ids = soup.findAll(href=re.compile("course_id="))
    courses = []
    for link in ids:
        href = link.get('href').split('course_id=')[-1]
        name = link.text.strip()
        courses.append((href, name))
    return courses

def sync_file(path_prefix, course_id):
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    soup = bs(get_page('MultiLanguage/lesson/student/download.jsp?course_id=' + str(course_id)), 'html.parser')
    threads = []
    for comment in soup(text=lambda text: isinstance(text, bs4.Comment)):
        link = bs(comment, 'html.parser').a
        name = link.text
        uri = comment.next.next.a.get('href')
        filename = link.get('onclick').split('getfilelink=')[-1].split('&id')[0]
        file_path = os.path.join(path_prefix, filename)
        file_path = NTFSSan(file_path)
        if not os.path.exists(file_path):
            print('Download ', name)
            download_thread = threading.Thread(target=download, args=(uri, file_path))
            threads.append(download_thread)
            download_thread.start()
    
    for thread in threads:
        thread.join()

def seek_hw(path, url):
    hw_path = path
    hw_path = NTFSSan(hw_path)
    if not os.path.exists(hw_path):
        os.makedirs(hw_path)
    soup = bs(get_page(url), 'html.parser')
    for link in soup.findAll('a'):
        name = 'upload-'+link.text if link.parent.previous.previous.strip() == '上交作业附件' else link.text
        uri = link.get('href')
        file_path = os.path.join(hw_path, name)
        file_path = NTFSSan(file_path)
        if not os.path.exists(file_path):
            print('Download ', name)
            download(uri, file_path)

def sync_hw(path_prefix, course_id):
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    root = bs(get_page('MultiLanguage/lesson/student/hom_wk_brw.jsp?course_id=' + str(course_id)), 'html.parser')
    threads = []
    for ele in root.findAll('a'):
        seek_thread = threading.Thread(target=seek_hw, args=(os.path.join(path_prefix, ele.text), 'MultiLanguage/lesson/student/' + ele.get('href')))
        threads.append(seek_thread)
        seek_thread.start()
    
    for thread in threads:
        thread.join()

def sync_notification(path_prefix, course_id):
    if not os.path.exists(path_prefix):
        os.makedirs(path_prefix)
    final_url = urllib.request.urlopen(url+'MultiLanguage/public/bbs/getnoteid_student.jsp?course_id=' + str(course_id)).geturl()
    root = bs(get_page(final_url), 'html.parser')
    for ele in root.findAll('a'):
        note_path = os.path.join(path_prefix, ele.text+'.html')
        note_path = NTFSSan(note_path)
        uri = 'MultiLanguage/public/bbs/' + ele.get('href')
        uri = iriToUri(uri)
        if not os.path.exists(note_path):
            print('Copy note ' + ele.text)
            print('From ' + uri)
            download(uri, note_path)

if __name__ == '__main__':
    ignore = codecs.open('file.ignore', mode='r', encoding='utf-8').read().split() if os.path.exists('file.ignore') else []
    os.remove(large_file_list)
    os.remove(failed_file_list)
    username = input('username: ')
    password = getpass.getpass('password: ')
    if login(username, password):
        last_login = time.monotonic()
        typepage = 1 if '.py' in sys.argv[-1] else int(sys.argv[-1])
        courses = get_courses(typepage)
        for course_id, name in courses:
            current = time.monotonic()
            if current - last_login > 1800:
                # half an hour
                login(username, password)
                last_login = time.monotonic()
            if name in ignore:
                print('Skip ' + name)
            else:
                print('Sync '+ name)
                sync_file(os.path.join(name, "Files"), course_id)
            sync_hw(os.path.join(name, "Homework"), course_id)
            sync_notification(os.path.join(name, "Notes"), course_id)
