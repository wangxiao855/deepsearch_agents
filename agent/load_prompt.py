"""
读取yaml文件里的配置信息
"""
from pathlib import Path
import yaml

#创建读取yaml文件函数
def load_yaml(file_path:str):
    #读取文件
    with open(file_path,"r",encoding="utf-8") as f:
        #将yaml文件读取为字典
        return yaml.safe_load(f)
#获取prompt.yaml的绝对路径
yaml_path = Path(__file__).parent.parent / "prompt" / "prompts.yaml"
#调用load_yaml，获取yaml文件转换的字典
yaml_dict = load_yaml(yaml_path)

#获取main_agent的配置信息
main_agent_config = yaml_dict["main_agent"]
#获取subagents的配置信息
sub_agents_config = yaml_dict["sub_agents"]


if __name__ == '__main__':
    print(yaml_dict)
    print(main_agent_config)
    print(sub_agents_config)



