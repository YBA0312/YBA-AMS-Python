import paho.mqtt.client as mqtt
import ssl
import json
import socket
import time

DEBUG = False

################## 以下为用户配置区 ##################
MQTT_SERVER = "ip_address"  # 将ip_address替换为打印机的IP地址
PASSWORD = "your_password"  # 将your_password替换为局域网模式里的密码
DEVICE_SERIAL = "your_device_serial"  # 将your_device_serial替换为设备的序列号
TCP_SERVER = "ip_address"  # 将ip_address替换为驱动板的IP地址
##################### 参数配置 ######################
CH_MAP = [1, 2, -1, -1]  # 通道映射表
CH_RE_LEN = [3, 3, 3, 3] # 通道抽回时间（秒）
CH_AF = [1, 1, 1, 1] # 通道辅助送料开关
filament_current = 0 # 当前通道
################## 以上为用户配置区 ##################

# 定义服务器信息和认证信息
TCP_PORT = 3333
MQTT_PORT = 8883
MQTT_VERSION = mqtt.MQTTv311
USERNAME = "bblp"

# 创建一个TCP/IP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# 订阅和发送的主题
TOPIC_SUBSCRIBE = f"device/{DEVICE_SERIAL}/report"
TOPIC_PUBLISH = f"device/{DEVICE_SERIAL}/request"

step = 0
filament_next = -1
ch_state = [0,0,0,0]

bambu_resume = '{"print":{"command":"resume","sequence_id":"1"},"user_id":"1"}'
bambu_unload = '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":220,"target":255},"user_id":"1"}'
bambu_load = '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":220,"target":254},"user_id":"1"}'
bambu_done = '{"print":{"command":"ams_control","param":"done","sequence_id":"1"},"user_id":"1"}'
ams_head = b'\x2f\x2f\xff\xfe\x01\x02' # 先用着，后面再改

def connect_to_server(server_ip, server_port):
    """尝试连接到服务器，并返回socket对象"""
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((server_ip, server_port))
            print("连接到AMS成功")
            # send_ams(ams_head + bytes([CH_MAP[filament_current]]) + bytes([1])) # 输送当前通道
            return sock
        except Exception as e:
            print(f"连接到AMS失败: {e}")
            print("5秒后尝试重新连接...")
            time.sleep(5)

# 向AMS发送指令
def send_ams(data):
    global sock
    """发送数据到服务器，如果连接断开，自动重新连接并重新发送"""
    while True:
        try:
            sock.sendall(data)
            if DEBUG:
                print("向AMS发送成功")
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

# 当收到服务器发来的消息时的回调
def on_message(client, userdata, message):
    global step, filament_next, filament_current
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
                            print("开始换色")
                            if DEBUG:
                                print(f"颜色: {json_data['print']['mc_remaining_time']}")
                            filament_next = find_channel(json_data["print"]["mc_remaining_time"] + 1) # 更换通道
                            print(f"当前通道：{filament_current} 下一个通道: {filament_next}")
                            if (filament_next == -1):
                                print("未找到对应通道，或耗材已耗尽")
                                return
                            if filament_next == filament_current: # 无需更换
                                print("无需更换")
                                client.publish(TOPIC_PUBLISH, bambu_resume, qos=2) # 继续打印
                                return
                            client.publish(TOPIC_PUBLISH, bambu_unload, qos=2) # 卸载耗材丝
                            print("等待卸载完成")
                            step = 2
                            ams_control(filament_current, 2) # 抽回当前通道
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
                    client.publish(TOPIC_PUBLISH, bambu_load, qos=2)
                    step = 4
        elif step == 4:
            if "ams" in json_data["print"]:
                if "tray_tar" in json_data["print"]["ams"]:
                    if CH_AF[filament_next] == 0:
                        ams_control(filament_next, 0) # 停止输送
                    time.sleep(8) # 等待装载完成 TODO:这里可以通过“print_error: 318734343”来判断转载完成，先用延时省事
                    print("装载完成")
                    client.publish(TOPIC_PUBLISH, bambu_done, qos=2)
                    time.sleep(2) # 等待，可能不需要
                    client.publish(TOPIC_PUBLISH, bambu_resume, qos=2)
                    print("换色完成")
                    step = 1
        # elif step == 5:
        #     if "ams" in json_data["print"]:
        #         if "tray_pre" in json_data["print"]["ams"]:
        #             if json_data["print"]["ams"]["tray_pre"] == 254:
        #                 print("冲刷完成，继续打印")
        #                 client.publish(TOPIC_PUBLISH, bambu_resume, qos=2)
        #                 step = 1
                    

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

try:
    while True:
       if DEBUG:
        for i in range(4):
            ams_control(i, ch_state[i]) # 心跳+同步状态 先这样写，后面再改
            time.sleep(0.3)
        time.sleep(1)
       pass
except KeyboardInterrupt:
    # send_ams(ams_head + bytes([CH_MAP[filament_current]]) + bytes([0])) # 停止当前通道
    print("Exiting")
    time.sleep(1000)
    client.disconnect()
