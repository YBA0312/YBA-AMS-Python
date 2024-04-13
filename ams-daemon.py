import json
import socket
import ssl
import time
import sys

import paho.mqtt.client as mqtt

DEBUG = False
sys.stdout = sys.stderr

################## 以下为用户配置区 ##################
MQTT_SERVER = ""  # 将ip_address替换为打印机的IP地址
PASSWORD = ""  # 将your_password替换为局域网模式里的密码
DEVICE_SERIAL = ""  # 将your_device_serial替换为设备的序列号
TCP_SERVER = ""  # 将ip_address替换为驱动板的IP地址
##################### 参数配置 ######################
CH_DEF = -1 # 当前通道
F_CG_T = "255" # 换色温度
CH_MAP = [1, 2, -1, -1]  # 通道映射表
CH_RE_LEN = [2, 2, 2, 2] # 通道抽回时间（秒）
CH_AF = [1, 1, 1, 1] # 通道辅助送料开关
USE_PRINTER_UNLOAD = False   # 使用打印退料，(记得把换料gcode里的退料取消)
# 换料gcode中移除以下代码
# G1 X180 F18000
# G1 Y180 F3000
# G1 X200 F1500
# G1 E-2 F500
# G1 X180 F3000
################## 以上为用户配置区 ##################

# 定义服务器信息和认证信息
TCP_PORT = 3333
MQTT_PORT = 8883
MQTT_VERSION = mqtt.MQTTv311
USERNAME = "bblp"

# 读取 JSON 文件
def read_json_file(file_path):
    try:
        with open(file_path, 'r') as file:
            data = json.load(file)
            return data
    except FileNotFoundError:
        #print(f"找不到文件：{file_path}")
        return None
    except ValueError as e:
        print(f"解析 JSON 失败：{e}")
        return None

# 写入 JSON 文件
def write_json_file(file_path, data):
    try:
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=4)
            return True
    except FileNotFoundError:
        print(f"找不到文件：{file_path}")
        return False
    except IOError as e:
        print(f"写入 JSON 失败：{e}")
        return False

def get_input_with_default(prompt, default_value):
    user_input = input(f"{prompt} ({default_value}): ")
    return user_input if user_input else default_value

def get_boolean_input(prompt, default_value):
    user_input = input(f"{prompt} ({'Y/n' if default_value else 'y/N'}): ").lower()
    if user_input == '':
        return default_value
    else:
        return user_input == 'y'

# 读取 配置 文件
read_data = read_json_file('config.json')
if read_data is None:
    while MQTT_SERVER == "":
        MQTT_SERVER = input("打印机IP地址：")
    while PASSWORD == "":
        PASSWORD = input("打印机局域网密码：")
    while DEVICE_SERIAL == "":
        DEVICE_SERIAL = input("打印机序列号：")
    while TCP_SERVER == "":
        TCP_SERVER = input("AMS驱动板IP地址: ")
else:
    MQTT_SERVER = read_data["MQTT_SERVER"]
    PASSWORD = read_data["PASSWORD"]
    DEVICE_SERIAL = read_data["DEVICE_SERIAL"]
    TCP_SERVER = read_data["TCP_SERVER"]
    F_CG_T = read_data["F_CG_T"]
    CH_DEF = read_data["CH_DEF"]
    USE_PRINTER_UNLOAD = read_data["USE_PRINTER_UNLOAD"]
    print('成功读取配置文件，如果想修改配置可打开"config.json"进行配置，或者删除改文件重新生成')

if CH_DEF == -1:
    CH_DEF = int(get_input_with_default(f"当前通道", CH_DEF))
    F_CG_T = get_input_with_default("换色温度", F_CG_T)
    USE_PRINTER_UNLOAD = get_boolean_input("使用打印机退料", USE_PRINTER_UNLOAD)

if USE_PRINTER_UNLOAD:
    print('提示：当前使用打印机默认退料方式, 请确保移除换料gcode中的切料代码')

read_data = {"MQTT_SERVER": MQTT_SERVER, "PASSWORD": PASSWORD, "DEVICE_SERIAL": DEVICE_SERIAL, "TCP_SERVER": TCP_SERVER, "F_CG_T": F_CG_T, "CH_DEF": CH_DEF, "USE_PRINTER_UNLOAD": USE_PRINTER_UNLOAD}
write_json_file('config.json', read_data)

# 创建一个TCP/IP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# 订阅和发送的主题
TOPIC_SUBSCRIBE = f"device/{DEVICE_SERIAL}/report"
TOPIC_PUBLISH = f"device/{DEVICE_SERIAL}/request"

step = 0
filament_current = 0 # 当前通道
filament_next = -1
ch_state = [0,0,0,0]

cg_num = 0

bambu_resume = '{"print":{"command":"resume","sequence_id":"1"},"user_id":"1"}'
bambu_unload = '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":220,"target":255},"user_id":"1"}'
bambu_load = '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":220,"target":254},"user_id":"1"}'
bambu_done = '{"print":{"command":"ams_control","param":"done","sequence_id":"1"},"user_id":"1"}'
bambu_clear = '{"print":{"command": "clean_print_error","sequence_id":"1"},"user_id":"1"}'
bambu_status = '{"pushing": {"sequence_id": "0", "command": "pushall"}}'
ams_head = b'\x2f\x2f\xff\xfe\x01\x02' # 先用着，后面再改

def connect_to_server(server_ip, server_port):
    """尝试连接到服务器，并返回socket对象"""
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((server_ip, server_port))
            print("连接到AMS成功")
            return sock
        except Exception as e:
            print(f"连接到AMS失败: {e}")
            print("5秒后尝试重新连接...")
            time.sleep(5)

# 向AMS发送指令
def send_ams(data):
    global sock, TCP_SERVER
    """发送数据到服务器，如果连接断开，自动重新连接并重新发送"""
    while True:
        try:
            sock.sendall(data)
            # if DEBUG:
            #     print("向AMS发送成功")
            return
        except Exception as e:
            print(f"向AMS发送指令失败: {e}")
            print("尝试重新连接...")
            sock = connect_to_server(TCP_SERVER, TCP_PORT)
            print("重新连接成功，尝试再次发送")

# AMS控制
def ams_control(ch, fx):
    global ch_state
    send_ams(ams_head + bytes([ch]) + bytes([fx]))
    ch_state[ch] = fx

# 查找耗材对应的通道
def find_channel(filament):
    global CH_MAP
    for i in range(len(CH_MAP)):
        if CH_MAP[i] == filament:
            return i
    return -1

# 当客户端接收到来自服务器的CONNACK响应时的回调
def on_connect(client, userdata, flags, rc, properties):
    global step
    if rc == 0:
        print("连接竹子成功")
        # 连接成功后订阅主题
        client.subscribe(TOPIC_SUBSCRIBE)
        step = 1
    else:
        print(f"连接竹子失败，错误代码 {rc}")

# 当客户端断开连接时的回调
def on_disconnect(client, userdata, disconnect_flags, reason_code, propertie):
    global step
    print("连接已断开，请检查打印机状态，以及是否有其它应用占用了打印机")
    reconnect(client)
    # step = 0

def reconnect(client, delay=3):
    while True:
        print("尝试重新连接竹子...")
        try:
            client.reconnect()
            break  # 重连成功则退出循环
        except:
            print(f"重连竹子失败 {delay} 秒后重试...")
            time.sleep(delay)  # 等待一段时间后再次尝试

def publish_gcode(client, g_code):
    operation_code = '{"print": {"sequence_id": "1", "command": "gcode_line", "param": "'+g_code+'"},"user_id":"1"}'
    client.publish(TOPIC_PUBLISH, operation_code)

def publish_resume(client):
    # piblish_gcode(client, "G1 E4 F200")
    client.publish(TOPIC_PUBLISH, bambu_resume)

def publish_unload(client, unloadTemp = 255):
    client.publish(TOPIC_PUBLISH, 
                   '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":' + 
                   str(unloadTemp) 
                   + ',"target":255},"user_id":"1"}')
    
# 当收到服务器发来的消息时的回调
def on_message(client, userdata, message):
    global step, filament_next, filament_current, cg_num
    global F_CG_T
    if DEBUG:
        print(f"Received message '{str(message.payload.decode('utf-8'))}' on topic '{message.topic}'")
    try:
        # 尝试解析JSON数据
        payload = str(message.payload.decode('utf-8'))
        json_data = json.loads(payload)
        if DEBUG:
            print(json_data)
        # 这里可以根据需要进一步处理json_data
    except json.JSONDecodeError:
        # 如果消息不是JSON格式，打印错误
        print("JSON解析失败")
        return
    if "print" in json_data:
        if step == 1:
            if "gcode_state" in json_data["print"]:
                if json_data["print"]["gcode_state"] == "PAUSE": # 暂停状态
                    if "mc_percent" in json_data["print"] and "mc_remaining_time" in json_data["print"]:
                        if json_data["print"]["mc_percent"] == 101: # 换色指令
                            cg_num = cg_num + 1
                            print("########## 开始第 " + str(cg_num) + " 次换色 ##########")
                            if DEBUG:
                                print(f"颜色: {json_data['print']['mc_remaining_time']}")
                            filament_next = find_channel(json_data["print"]["mc_remaining_time"] + 1) # 更换通道
                            print(f"当前AMS通道：{filament_current+1} 下一个AMS通道: {filament_next+1}")
                            if (filament_next == -1):
                                print("未找到对应AMS通道，或耗材已耗尽")
                                return
                            if filament_next == filament_current: # 无需更换
                                print("无需更换")
                                publish_resume(client) # 继续打印
                                return
                            
                            ams_control(filament_current, 2) # 抽回当前通道
                            publish_gcode(client, "G1 E-25 F500\nM109 S" + F_CG_T + "\n") # 抽回一段距离，提前升温

                            if USE_PRINTER_UNLOAD:
                                publish_unload(client, F_CG_T) # 调用打印机卸载耗材

                            print("等待卸载完成")
                            step = 2
        elif step == 2:
            if "hw_switch_state" in json_data["print"]:
                if json_data["print"]["hw_switch_state"] == 0: # 断料检测为无料
                    print("卸载完成")
                    time.sleep(CH_RE_LEN[filament_current]) # 等待抽回一段距离
                    ams_control(filament_current, 0) # 停止抽回
                    filament_current = -1
                    step = 3
                    time.sleep(1)
                    ams_control(filament_next, 1) # 输送下一个通道
        elif step == 3:
            if "hw_switch_state" in json_data["print"]:
                if json_data["print"]["hw_switch_state"] == 1: # 断料检测为有料
                    print("料线到达，开始装载")
                    filament_current = filament_next
                    # piblish_gcode(client, "G1 E4 F200") # 输送一段距离
                    time.sleep(2)
                    print(f"换色完成，当前AMS通道：{filament_next+1}")
                    publish_resume(client)
                    time.sleep(5)
                    if CH_AF[filament_next] == 0:
                        ams_control(filament_next, 0) # 停止输送
                    client.publish(TOPIC_PUBLISH, bambu_clear)
                    step = 1
                    read_data = {"MQTT_SERVER": MQTT_SERVER, "PASSWORD": PASSWORD, "DEVICE_SERIAL": DEVICE_SERIAL, "TCP_SERVER": TCP_SERVER, "F_CG_T": F_CG_T, "CH_DEF": filament_next+1, "USE_PRINTER_UNLOAD": USE_PRINTER_UNLOAD}
                    write_json_file('config.json', read_data)

# 创建MQTT客户端实例
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="yba-ams")

# 设置TLS
client.tls_set(cert_reqs=ssl.CERT_NONE)  # 如果服务器使用自签名证书，请使用ssl.CERT_NONE
client.tls_insecure_set(True)  # 只有在使用自签名证书时才设置为True

# 设置用户名和密码
client.username_pw_set(USERNAME, PASSWORD)

# 设置回调函数
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# 连接到MQTT服务器
client.connect(MQTT_SERVER, MQTT_PORT, 60)

# 订阅主题
client.subscribe(TOPIC_SUBSCRIBE, qos=1)

# 启动循环，以便客户端能够处理回调
client.loop_start()

# 示例：向发送主题发送消息
# client.publish(TOPIC_PUBLISH, "Your message here")

# 连接AMS
sock = connect_to_server(TCP_SERVER, TCP_PORT)
filament_current =  find_channel(CH_DEF)
if filament_current == -1:
    print("未找到默认通道")
    exit(1)
ams_control(filament_current, 1)

try:
    while True:
        for t in range(5):
            for i in range(4):
                ams_control(i, ch_state[i]) # 心跳+同步状态 先这样写，后面再改
                time.sleep(0.3)
            time.sleep(1)
        if step == 1:
            client.publish(TOPIC_PUBLISH, bambu_status)
except KeyboardInterrupt:
    send_ams(ams_head + bytes([CH_MAP[filament_current]]) + bytes([0])) # 停止当前通道
    print("Exiting")
    time.sleep(1000)
    client.disconnect()
