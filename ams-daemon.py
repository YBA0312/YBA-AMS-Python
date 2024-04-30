import json
import socket
import ssl
import time
import sys
import csv
import os

import paho.mqtt.client as mqtt

DETAIL = True
DEBUG = True
sys.stdout = sys.stderr

################## 以下为用户配置区 ##################
MQTT_SERVER = ""  # 将ip_address替换为打印机的IP地址
PASSWORD = ""  # 将your_password替换为局域网模式里的密码
DEVICE_SERIAL = ""  # 将your_device_serial替换为设备的序列号
##################### 参数配置 ######################
CH_DEF = -1 # 当前通道
F_CG_T = "255" # 换色温度
USE_PRINTER_UNLOAD = False   # 使用打印退料，(记得把换料gcode里的退料取消)、
FILAMENT_AUTO_FILL = False
instances = []
 
# 换料gcode中移除以下代码
# G1 X180 F18000
# G1 Y180 F3000
# G1 X200 F1500
# G1 E-2 F500
# G1 X180 F3000
################## 以上为用户配置区 ##################
channel_transport_timeout = 30
rollback_time = 2
# 定义服务器信息和认证信息
TCP_PORT = 3333
TCP_HEALTH_PORT = 3334
MQTT_PORT = 8883
MQTT_VERSION = mqtt.MQTTv311
USERNAME = "bblp"
csv_file_path = "data.csv"

def save_instance(data):
    if os.path.exists(csv_file_path):
        print("instance exist")
        with open(csv_file_path, mode='a', newline='') as file:
            writer = csv.writer(file)
            for row in data:
                writer.writerow(row)
        print("write complete")
    else:
        print("data not exist")
        with open(csv_file_path, mode='w', newline='') as file:
            writer = csv.writer(file)
            for row in data:
                writer.writerow(row)        
        print("write complete")

class Instance:
    def __init__(self, index, id, ip, channel, sock):
        self.index = index
        self.id = id
        self.ip = ip
        self.channel = channel
        self.sock = sock
    
    def __str__(self):
        if self.sock == None:
            return f"({self.index}, {self.id}, {self.ip}, {self.channel}, None)"
        else: 
            return f"({self.index}, {self.id}, {self.ip}, {self.channel}, exist)"

class InstanceData:
    def __init__(self, index, id, ip, channel):
        self.index = index
        self.id = id
        self.ip = ip
        self.channel = channel
    
    def __iter__(self):
        return iter([self.index, self.id, self.ip, self.channel])

     
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
    # while FILAMENT_AUTO_FILL == "":
    #     FILAMENT_AUTO_FILL = input("自动续料 true/false: ")
else:
    MQTT_SERVER = read_data["MQTT_SERVER"]
    PASSWORD = read_data["PASSWORD"]
    DEVICE_SERIAL = read_data["DEVICE_SERIAL"]
    F_CG_T = read_data["F_CG_T"]
    CH_DEF = read_data["CH_DEF"]
    USE_PRINTER_UNLOAD = read_data["USE_PRINTER_UNLOAD"]
    FILAMENT_AUTO_FILL = read_data["FILAMENT_AUTO_FILL"]
    print('成功读取配置文件，如果想修改配置可打开"config.json"进行配置，或者删除改文件重新生成')

if CH_DEF == -1:
    CH_DEF = int(get_input_with_default(f"当前通道", CH_DEF))
    F_CG_T = get_input_with_default("换色温度", F_CG_T)
    USE_PRINTER_UNLOAD = get_boolean_input("使用打印机退料", USE_PRINTER_UNLOAD)

if USE_PRINTER_UNLOAD:
    print('提示：当前使用打印机默认退料方式, 请确保移除换料gcode中的切料代码')
if FILAMENT_AUTO_FILL:
    print('提示：如使用自动续料，无法保证多色颜色顺序')

read_data = {"MQTT_SERVER": MQTT_SERVER, "PASSWORD": PASSWORD, "DEVICE_SERIAL": DEVICE_SERIAL, "F_CG_T": F_CG_T, "CH_DEF": CH_DEF, "USE_PRINTER_UNLOAD": USE_PRINTER_UNLOAD, "FILAMENT_AUTO_FILL": FILAMENT_AUTO_FILL}
write_json_file('config.json', read_data)

# 订阅和发送的主题
TOPIC_SUBSCRIBE = f"device/{DEVICE_SERIAL}/report"
TOPIC_PUBLISH = f"device/{DEVICE_SERIAL}/request"

step = 0
filament_current = 0 # 当前通道
filament_next = -1
channel_filament_state = [0,0,0,0] #4通道以上需要注册待修改
temp_channel = []
cg_num = 0

bambu_resume = '{"print":{"command":"resume","sequence_id":"1"},"user_id":"1"}'
bambu_unload = '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":220,"target":255},"user_id":"1"}'
bambu_load = '{"print":{"command":"ams_change_filament","curr_temp":220,"sequence_id":"1","tar_temp":220,"target":254},"user_id":"1"}'
bambu_done = '{"print":{"command":"ams_control","param":"done","sequence_id":"1"},"user_id":"1"}'
bambu_clear = '{"print":{"command": "clean_print_error","sequence_id":"1"},"user_id":"1"}'
bambu_status = '{"pushing": {"sequence_id": "0", "command": "pushall"}}'
ams_head = b'\x2f\x2f\xff\xfe\x01\x02' # 先用着，后面再改
def connect_to_server(server_ip, server_port, sock):
    while True:
        try:
            if sock:
                print("清理sock...")
                sock.close()
            else: 
                print(f"尝试连接AMS:{server_ip}:{server_port}")
                new_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # new_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                new_sock.connect((server_ip, server_port))
                print(f"连接AMS实例:{server_ip}:{server_port}成功")
                return new_sock
        except Exception as e:
            print(f"连接到AMS失败: {e}")
            print("5秒后尝试重新连接...")
            time.sleep(5)

# 关闭广播
def close_broadcast(server_ip, port):
    closeSock = None
    data = ams_head + bytes([255]) + bytes([0])
    while True:
        try:
            print(f"尝试关闭实例:{server_ip}广播")
            closeSock = connect_to_server(server_ip, port, None)
            closeSock.sendall(data)
            closeSock.close()
            return
        except Exception as e:
            print(f"关闭广播失败: {e}")
            closeSock = connect_to_server(server_ip, port, None)

# 向AMS发送指令
def ams_control(ch, fx):
    ams_sock = None
    ip = ""
    # print(f"{ch}")
    data = ams_head + bytes([ch]) + bytes([fx])
    while True:
        try:
            for i in range(len(instances)):
            #    print(f"{str(instances[i])}")
               if int(instances[i].channel) == ch:
                    if instances[i].sock:
                        ams_sock = instances[i].sock
                        ip = instances[i].ip
                    else:
                        ams_sock = connect_to_server(instances[i].ip, TCP_PORT, None)
                        if ams_sock:
                            instances[i].sock = ams_sock
                    if ip == instances[i].ip:
                        ams_sock.sendall(data)
                        return
            if ams_sock:
                ams_sock.sendall(data)
                return
        except Exception as e:
            print(f"向AMS发送指令失败: {e}")
            instance_id = ""
            for i in range(len(instances)):
                instance_id = instances[i].id
                if int(instances[i].channel) == ch:
                    # if instances[i].sock:
                    #     instances[i].sock.close()                    
                    instances[i].sock = None
                if instances[i].id == instance_id:
                    # if instances[i].sock:
                    #     instances[i].sock.close()                    
                    instances[i].sock = None
            time.sleep(5)


# 查找耗材对应的通道
def find_channel(filament):
    for i in range(len(instances)):
        if filament - 1 == int(instances[i].channel):
            print(f"find channel: {i}")
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
    client.publish(TOPIC_PUBLISH, bambu_unload)
    
# 当收到服务器发来的消息时的回调
def on_message(client, userdata, message):
    global step, filament_next, filament_current, cg_num, temp_channel
    global F_CG_T
    if DETAIL:
        print(f"Received message '{str(message.payload.decode('utf-8'))}' on topic '{message.topic}'")
    try:
        # 尝试解析JSON数据
        payload = str(message.payload.decode('utf-8'))
        json_data = json.loads(payload)
        if DETAIL:
            print(json_data)
        # 这里可以根据需要进一步处理json_data
    except json.JSONDecodeError:
        # 如果消息不是JSON格式，打印错误
        print("JSON解析失败")
        return
    if "print" in json_data:
        if step == 1:
            if DEBUG:
                print(f"当前步骤{step}")
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
                            # 提前抽出所有通道
                            for j in range(len(instances)):
                                print(f"尝试抽出第{instances[j].index}通道")
                                ams_control(int(instances[j].index), 2)
                                time.sleep(rollback_time)
                                ams_control(int(instances[j].index), 0) 
                            for i in range(3):
                                print(f"尝试第{i}次退料")
                                ams_control(filament_current, 2)
                                time.sleep(rollback_time)
                                ams_control(filament_current, 0) 
                                ams_control(filament_current, 1) 
                                time.sleep(rollback_time)
                                ams_control(filament_current, 0) 
                            ams_control(filament_current, 2)

                            publish_gcode(client, "G1 E-25 F500\nM109 S" + F_CG_T + "\n") # 抽回一段距离，提前升温

                            if USE_PRINTER_UNLOAD:
                                publish_unload(client, F_CG_T) # 调用打印机卸载耗材

                            print("等待卸载完成")
                            step = 2
        elif step == 2:
            if DEBUG:
                print(f"当前步骤{step}")
            if "hw_switch_state" in json_data["print"]:
                if json_data["print"]["hw_switch_state"] == 0: # 断料检测为无料
                    print("卸载完成")
                    time.sleep(rollback_time) # 等待抽回一段距离
                    ams_control(filament_current, 0) # 停止抽回
                    filament_current = -1
                    time.sleep(1)
                    print(f"输送通道{filament_next}")
                    # 提前抽出所有通道
                    for j in range(len(instances)):
                        print(f"尝试抽出第{instances[j].index}通道")
                        ams_control(int(instances[j].index), 2)
                        time.sleep(rollback_time)
                        ams_control(int(instances[j].index), 0) 
                    ams_control(filament_next, 1) # 输送下一个通道
                    step = 3
                    time.sleep(channel_transport_timeout)
                    print(f"输送通道{filament_next}超时，停止输送")
                    # ams_control(filament_next, 0)
        elif step == 2.5:
            if DEBUG:
                print(f"当前步骤{step}")
            print(f"输送通道{filament_next}")
            # 提前抽出所有通道
            for j in range(len(instances)):
                print(f"尝试抽出第{instances[j].index}通道")
                ams_control(int(instances[j].index), 2)
                time.sleep(rollback_time)
                ams_control(int(instances[j].index), 0) 
            ams_control(filament_next, 1)
            step = 3
            time.sleep(channel_transport_timeout)
            print(f"输送通道{filament_next}超时，停止输送,待检查进料状态")
            # ams_control(filament_next, 0)
        elif step == 3:
            if DEBUG:
                print(f"当前步骤{step}")          
            if "hw_switch_state" in json_data["print"]:
                if json_data["print"]["hw_switch_state"] == 1: # 断料检测为有料
                    print("料线到达，开始装载")
                    channel_filament_state[filament_next] = 1
                    filament_current = filament_next
                    # piblish_gcode(client, "G1 E4 F200") # 输送一段距离
                    time.sleep(2)
                    print(f"换色完成，当前AMS通道：{filament_next+1}")
                    publish_resume(client)
                    time.sleep(5)
                    client.publish(TOPIC_PUBLISH, bambu_clear)
                    step = 1
                    CH_DEF = filament_next
                    read_data = {"MQTT_SERVER": MQTT_SERVER, "PASSWORD": PASSWORD, "DEVICE_SERIAL": DEVICE_SERIAL, "F_CG_T": F_CG_T, "CH_DEF": CH_DEF, "USE_PRINTER_UNLOAD": USE_PRINTER_UNLOAD, "FILAMENT_AUTO_FILL": FILAMENT_AUTO_FILL}
                    write_json_file('config.json', read_data)
            # else:
            #     if DEBUG:
            #       print("准备自动续料")          
            #     if FILAMENT_AUTO_FILL: 
            #         if len(temp_channel) == len(instances) :
            #              print("请及时补充耗材")
            #         elif len(temp_channel) == len(instances) - 1 :
            #             print(f"恢复默认通道 {CH_DEF}")
            #             filament_current = find_channel(CH_DEF)
            #             ams_control(filament_current, 1)
            #             client.publish(TOPIC_PUBLISH, bambu_resume)
            #             time.sleep(5)
            #             client.publish(TOPIC_PUBLISH, bambu_clear)
            #             ams_control(filament_current, 1)
            #             step = 1        
            #         else:
            #             print("开始自动续料")          
            #             channel_filament_state[filament_next] = 0
            #             temp_channel.append(filament_next)
            #             temp_channel = list(set(temp_channel))
            #             if len(temp_channel) == len(instances) - 1 :
            #                 print("耗材耗尽")          
            #                 return
            #             print(f"等待通道{filament_next}送料超时，输送下一个通道 {filament_next + 1}")
            #             for j in range(len(temp_channel)):
            #                 print(f"检查通道: {temp_channel}")  
            #                 if (temp_channel[j] == filament_next + 1):
            #                     print(f"无效通道:{filament_next + 1}")  
            #                 else:
            #                     filament_channel = find_channel(filament_next + 1) # 更换通道
            #                     if (filament_channel == -1):
            #                         print("未找到对应AMS通道，或耗材已耗尽")
            #                         break
            #                     print(f"下一个AMS通道: {filament_channel + 1}")  
            #                     step = 2.5
            #                     filament_next = filament_channel + 1
            #                     return
                             


        

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

udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_socket.bind(('0.0.0.0', 9999))
udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
 
if os.path.exists(csv_file_path):
    with open(csv_file_path, mode='r', newline='') as file:
        reader = csv.reader(file)
        instance = list(reader)
        ip = ""
        initSock = None
        for i in range(len(instance)):
            if ip != instance[i][1]:
                print(f"init sock {instance[i][2]}")
                initSock = connect_to_server(instance[i][2], TCP_PORT, None)
                if initSock:
                    instances.append(Instance(instance[i][0], instance[i][1], instance[i][2], instance[i][3], initSock))
                    ip = instance[i][1]
            else: 
                instances.append(Instance(instance[i][0], instance[i][1], instance[i][2],  instance[i][3], initSock))
else: 
    with open(csv_file_path, 'w'):
        pass
filament_current =  find_channel(CH_DEF)
if filament_current == -1:
    print("未找到默认通道，将按通道顺序依次进料")
    step = 3
    filament_next = 0
    filament_current = 0
else:
    # 提前抽出所有通道
    for j in range(len(instances)):
        print(f"尝试抽出第{instances[j].index}通道")
        ams_control(int(instances[j].index), 2)
        time.sleep(rollback_time)
        ams_control(int(instances[j].index), 0) 
    ams_control(filament_current, 1)
try:
    while True:
        data, address = udp_socket.recvfrom(1024)
        print(f"receive broadcast from {address}: {data.decode()}")
        if data.decode() and address:
            if DEBUG:
                for i in range(len(instances)):
                    print(f"{str(instances[i])}")
            if len(instances) > 0:
                for i in range(len(instances)):
                    if (instances[i].sock == None): 
                        sock = connect_to_server(instances[i].ip, TCP_PORT, None)
                        instances[i].sock = sock
                    if data.decode() == instances[i].id:
                        if address[0] != instances[i].ip:
                            print("old instance recover with different ip, update ip and sock")
                            sock = connect_to_server(address[0], TCP_PORT, instances[i].sock)
                            instances[i].sock = sock
                            instances[i].ip = address[0]
                            for i in range(4): 
                                instance.append(InstanceData(instances[i].index, data.decode(), address[0], instances[i].channel))
                            save_instance(instance)
                    else: 
                        print("new instance register")
                        sock = connect_to_server(address[0], TCP_PORT, None)
                        index = instances[len(instances)-1].channel
                        instance = []
                        for i in range(4): 
                            instances.append(Instance(index + i, data.decode(), address[0], i, sock))
                            instance.append(InstanceData(index + i, data.decode(), address[0], i))
                        save_instance(instance)
            else: 
                print("init first instance")
                sock = connect_to_server(address[0], TCP_PORT, None)
                instance = []
                for i in range(4): 
                    instance.append(InstanceData(i, data.decode(), address[0], i))
                    instances.append(Instance(i, data.decode(), address[0], i, sock))
                print("save instance")
                save_instance(instance)
            # close_broadcast(address[0], TCP_PORT)
            # for i in range(len(instances)):
            #     print(f"{str(instances[i])}")

        for t in range(5):
            for i in range(4):
                ams_control(i, 5) # 心跳+同步状态 先这样写，后面再改
                time.sleep(1)
            time.sleep(1)
        if step == 1:
            client.publish(TOPIC_PUBLISH, bambu_status)
except KeyboardInterrupt:
    # ams_control(bytes([find_channel(filament_current)]), bytes([0])) # 停止当前通道
    print("Exiting")
    time.sleep(1000)
    client.disconnect()
