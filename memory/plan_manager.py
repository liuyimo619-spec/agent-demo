from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolRuntime
from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from logger import logger
from decimal import Decimal
from langgraph.types import Command
from langchain_core.messages import ToolMessage,HumanMessage,AIMessage
#导入数据类
from memory.ORM_SQLAlchemy import Travel_Plans,SessionLocal,Travel_Plan_Workspace,Cold_Zone_Cities,Cold_zone_attractions
import json
from memory.domain_guard import run_plan_critic
def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"记忆系统出错，不影响主进程，请明确告知用户（异常：{type(error).__name__} - {str(error)})"
#1.最底层，单个活动
class Activity(BaseModel):
    time_slot:Literal["上午", "下午", "晚上"] = Field(description="时间段，只能是 '上午'、'下午'、'晚上'",max_length=2)
    place:str = Field(description="旅行的景点",max_length=20)
    meal:str = Field(description="吃饭的地点,若无则填'无'",max_length=15)
    place_cost:Decimal = Field(description="旅行的景点价格(纯数字),免费则填0",max_digits=7,decimal_places=2)
    meal_cost:Decimal = Field(description="吃饭的景点价格(纯数字),免费或无需求则填0",max_digits=7,decimal_places=2)
    transport_cost:Decimal = Field(description="交通花费(纯数字),免费则填0",max_digits=7,decimal_places=2)
    note:str = Field(description="旅行计划备注")
#2.中间层，一天的行程
class DayPlan(BaseModel):
    day_index: int = Field(description="第几天，如1、2、3", ge=1, le=999)
    day_of_week: Literal["周一", "周二", "周三", "周四", "周五", "周六", "周日"] = Field(description="当天对应的星期。必须从枚举值中选择。")
    activities: list[Activity] = Field(description="当天的行程安排，按时间顺序排列，如{'上午', '下午', '晚上''}")
    day_cost:Decimal = Field(Decimal("0"),description="禁止填写此字段",max_digits=8,decimal_places=2)
#3.最外层，整个旅行计划（不限天数）
class PlanData(BaseModel):
    city:str = Field(description="旅行目的地城市名，如 '北京'、'上海'",max_length=10)
    days: list[DayPlan] = Field(description="旅行计划表，按日期规划从第一天到最后一天")
    remarks:str = Field(default='无',description="整体旅行计划特别备注（不要超过100字）",max_length=120)
    cost:Decimal = Field(Decimal("0"),description="禁止填写此字段",decimal_places=2,max_digits=8)

class SavePlanInput(BaseModel):
    plan_data: PlanData = Field(description="旅行计划数据")
    summary: str = Field(default='无',description="将上一版修改为这一版的原因，初版则填'无'",max_length=50)
#查找用户所有的旅行地
async def _get_all_plan_id(runtime:ToolRuntime)->dict[str,list[int]]|str:
    """
    查询用户所有旅行地
    """
    try:
        # 获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("查找用户所有旅行地: 缺少用户 ID")
            return "错误：缺少用户 ID"
        # 构建查询语句并执行
        stmt = select(Travel_Plans.city,Travel_Plans.plan_id).where(Travel_Plans.user_id == user_id).order_by(Travel_Plans.plan_id.asc())
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            rows = result.all()
        # 处理查询结果
        key_list = {}
        for key,plan_id in rows:
            key_list.setdefault(key,[]).append(plan_id)
        return key_list
    except Exception as e:
        return tool_error_handler(e)
#根据城市查找该城市的所有plan_id
async def _list_plan_ids_by_city(runtime:ToolRuntime,city:str)->list:
    '''
    根据城市名称查询该城市的所有plan_id

    Args:
        retime:工具的运行时
        city:查询的城市名称
    Return:返回新到旧列表
    '''
    try:
        lis = await _get_all_plan_id(runtime)
        if not isinstance(lis,dict):
            return lis
        # 设置当前计划key
        return lis.get(city,[])
    except Exception as e:
        return tool_error_handler(e)

#固定城市模糊次数
async def _get_plan_id_by_city(runtime:ToolRuntime,city:str,offset:int=-1)->int|str:
    '''
    根据城市名称查询该城市对应的plan_id

    Args:
        runtime:工具的运行时
        city:查询的城市名称
        offset:偏移量，默认为-1，表示返回该城市最 新的plan_id
    Return:返回key/报错信息
    '''
    try:
        lis = await _list_plan_ids_by_city(runtime,city)
        if not isinstance(lis,list):
            return lis
        if not lis:
            return f'未找到{city}的旅行计划'
        try:
            return lis[offset]
        except IndexError:
            logger.error(f"固定城市模糊次数: 偏移量超出范围")
            return f'偏移量 {offset} 超出范围，总计划数为 {len(lis)}，可用偏移量范围为 {len(lis)-1} 到 {-len(lis)}'
    except Exception as e:
        return tool_error_handler(e)
get_plan_id_by_city = StructuredTool.from_function(
    coroutine=_get_plan_id_by_city,
    name="get_plan_id_by_city",
    description="根据城市名称查询该城市对应的plan_id"
)

#模糊旅行次数
async def _get_plan_key_by_offset(runtime:ToolRuntime,offset:int=-1) ->list[int]|str:
    '''
    根据偏移量查询旅行计划plan_id

    Args:
        runtime:工具的运行时
        offset:偏移量，默认为-1，表示返回最新的plan_id
    Return:返回key/报错信息
    '''
    try:
        # 获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("模糊旅行次数: 缺少用户 ID")
            return "错误：缺少用户 ID"
        # 构建查询语句并执行
        stmt = select(Travel_Plans.plan_id).where(Travel_Plans.user_id == user_id).order_by(Travel_Plans.plan_id.asc())
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            lis = result.scalars().all()
        if not lis:
            return "没有旅行计划"
        try:
            return lis[offset]
        except IndexError:
            logger.error(f"模糊旅行次数: 偏移量超出范围")
            return f'偏移量 {offset} 超出范围，总计划数为 {len(lis)}，可用偏移量范围为 {len(lis)-1} 到{-len(lis)}'
    except Exception as e:
        return tool_error_handler(e)

get_plan_key_by_offset = StructuredTool.from_function(
    coroutine=_get_plan_key_by_offset,
    name="get_plan_key_by_offset",
    description="根据偏移量查询旅行计划的plan_id"
)

#查询去过某城市多少次
async def _get_city_trip_count(runtime:ToolRuntime,city:str) -> int:
    '''
    查询去过某城市多少次

    Args:
        runtime:工具的运行时
        city:查询的城市名称
    Return:返回次数
    '''
    try:
        lis = await _list_plan_ids_by_city(runtime,city)
        if not isinstance(lis,list):
            return lis
        return len(lis)
    except Exception as e:
        return tool_error_handler(e)

get_city_trip_count = StructuredTool.from_function(
    coroutine=_get_city_trip_count,
    name="get_city_trip_count",
    description="查询去过某城市多少次"
)

#查询最新的旅行计划
async def _get_latest_plan(runtime:ToolRuntime)->str:
    '''
    查询最新已定稿的旅行计划
    '''
    try: 
        # 获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("查询最新旅行计划: 缺少用户 ID")
            return '缺少用户 ID'
        # 构建查询语句并执行
        stmt = select(Travel_Plans.itinerary,Travel_Plans.summary).where(Travel_Plans.user_id == user_id).order_by(Travel_Plans.plan_id.desc()).limit(1)
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            row = result.one_or_none()
        if row is not None:
            itinerary,summary = row
            return f'计划表：{itinerary}，历史记忆：{summary}'
        else:
            return "没有旅行计划"
    
    except Exception as e:
        return tool_error_handler(e)

get_latest_plan = StructuredTool.from_function(
    coroutine=_get_latest_plan,
    name="get_latest_plan",
    description="查询最新已定稿的旅行计划"
)

#通过pan_id查询旅行计划
async def _get_plan_by_plan_id(runtime:ToolRuntime,plan_id:int)->str:
    '''
    通过plan_id查询已定稿的旅行计划和历史记忆

    Args:
        runtime:工具的运行时
        plan_id:旅行计划plan_id，请先通过各种工具拿到准确的plan_id
    Return:返回旅行计划
    '''
    try:
        # 获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("通过plan_id查询旅行计划: 缺少用户 ID")
            return "缺少用户 ID"
        # 构建查询语句并执行
        stmt = select(Travel_Plans.itinerary,Travel_Plans.summary).where(Travel_Plans.plan_id == plan_id,Travel_Plans.user_id == user_id)
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            row = result.one_or_none()
        if row is not None:
            itinerary,summary = row
            return f'计划表：{itinerary}，历史记忆：{summary}'
        else:
            return "没有旅行计划"
    except Exception as e:
        return tool_error_handler(e)
get_plan_by_plan_id = StructuredTool.from_function(
    coroutine=_get_plan_by_plan_id,
    name="get_plan_by_plan_id",
    description="通过plan_id查询已定稿的旅行计划和历史记忆"
)

#删除旅行计划
async def _delete_plan(runtime:ToolRuntime,plan_id:int)->str:
    '''
    删除已定稿的旅行计划 需指定plan_id 危险操作请谨慎

    Args:
        runtime:工具的运行时
        plan_id:旅行计划plan_id,请先通过各种工具拿到准确的plan_id
    Return:返回操作结果
    '''
    try:
        # 获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("删除旅行计划: 缺少用户 ID")
            return "缺少用户 ID"
        # 构建删除语句并执行
        stmt_delete = delete(Travel_Plans).where(Travel_Plans.plan_id == plan_id,Travel_Plans.user_id == user_id)
        async with SessionLocal.begin() as session:
            stmt_result = await session.execute(stmt_delete)
        if stmt_result.rowcount == 0:
            return "删除失败"
        return "删除成功"
    except Exception as e:
        return tool_error_handler(e)
delete_plan = StructuredTool.from_function(
    coroutine=_delete_plan,
    name="delete_plan",
    description="删除已定稿的旅行计划,需指定plan_id 危险操作请谨慎"
)





#短期记忆（工作台）
#内部函数
#计算下一个可用的版本号
async def _generate_plan_key(user_id, city)->int:
    # 构建查询语句并执行
    stmt = select(func.max(Travel_Plan_Workspace.version_no)).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.city == city)
    async with SessionLocal() as session:
        version_no = await session.execute(stmt)
        version_no = version_no.scalar()
    #如果存在则版本号加1如果不存在则返回1 
    if version_no is not None:
        return version_no + 1
    else:
        return 1

#写入计划表到工作台
async def _save_plan(runtime: ToolRuntime,plan_data: PlanData,summary: Optional[str] = None,):
    """
    旅行计划表构建工具，会自动生成计划表以及版本号返回旅行计划表,保存到工作台并发送给用户，禁止复述计划表内容。
    """
    try:
        # 获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("写入短期记忆： 缺少用户 ID")
            return "错误：缺少用户 ID"
        # 检查计划表
        name_opentime_list = ""
        human_message = ""
        for messages in reversed(runtime.state.get('messages',[])):
            if isinstance(messages,ToolMessage) and messages.name == "maps_search_detail":
                content = messages.content
                if isinstance(content,str):
                    text = content
                elif isinstance(content, list) and content and isinstance(content[0], dict):
                    text = content[0].get("text", "")
                else:
                    continue
                try:
                    detail = json.loads(text)
                except json.JSONDecodeError:
                    continue
                place_name = detail.get("name", "")
                opentime2 = detail.get("opentime2", "")
                name_opentime = f"{place_name}:{opentime2}"
                name_opentime_list += name_opentime + "\n"
            if isinstance(messages,HumanMessage):
                human_message += messages.content +'\n'
        aimessage = f'计划表：{plan_data.model_dump_json()}材料：{name_opentime_list}'
        #构建对话
        att_na_op = [
            AIMessage(content=aimessage),
            HumanMessage(content=human_message),
        ]
        verdict = await run_plan_critic(att_na_op)
        if verdict == '系统错误':
            return Command(update={
                            'messages':[ToolMessage(content=f'计划表评判员异常，本次计划未保存，请明确告知用户。',status="error",tool_call_id=runtime.tool_call_id)]
                        })            
        elif verdict.decision == '合理':
            # 计算总价
            cost = 0
            for days in plan_data.days:
                days_cost = 0
                for action in days.activities:
                    days_cost += action.place_cost + action.meal_cost + action.transport_cost
                days.day_cost = days_cost
                cost += days.day_cost
            plan_data.cost = cost
            #获取版本号
            city = plan_data.city
            version_no = await _generate_plan_key(user_id, city)
            itinerary = plan_data.model_dump_json() #将pydantic对象转换为json字符串
            #构建插入语句并执行
            stmt = (insert(Travel_Plan_Workspace).values(user_id=user_id,city=city,version_no=version_no,itinerary=itinerary,summary=summary,))
            async with SessionLocal.begin() as session:
                await session.execute(stmt) 
            return Command(update={
                'messages':[ToolMessage(content=f'计划表已保存到工作台并发给用户，该计划表版本号为{version_no}，禁止复述计划表内容。',tool_call_id=runtime.tool_call_id,artifact={'render_type':'plan_card', 'markdown':itinerary})]
            })
        else:
            return Command(update={'messages':[ToolMessage(content=f'【计划表被打回】{verdict.reason}。先用一句话（≤25字）告诉用户打回点并说明正在修补，'
                f'然后按原因修改计划表并重新调用 save_plan；不要重复提交相同内容，不要等待用户确认。',status="error", tool_call_id=runtime.tool_call_id)]
                })
        
    except Exception as e:
        return tool_error_handler(e)
save_plan = StructuredTool.from_function(
    coroutine=_save_plan,
    name="save_plan",
    description="旅行计划表构建工具，会自动生成计划表以及版本号返回旅行计划表,保存到工作台并发送给用户，禁止复述计划表内容。若返回【计划表被打回】，先用一句话说明打回点并修补，再重新保存，不要重复提交相同版本。",
    args_schema=SavePlanInput
)


#读取短期记忆
async def _get_plan_summaries(runtime:ToolRuntime,city:str)-> list[str]:
    """
    读取指定城市的短期记忆

    Args:
        runtime:工具的运行时
        city:城市名称
    Return:返回操作结果
    """
    try:#获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("读取短期记忆： 缺少用户 ID")
            return "错误：缺少用户 ID"
        #构建查询语句并执行
        stmt = select(Travel_Plan_Workspace.summary).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.city == city,Travel_Plan_Workspace.end_time > func.now())
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            result = result.scalars().all()
        if result:
            return result
        else:
            return "没有找到该城市的短期记忆"
    except Exception as e:
        return tool_error_handler(e)
get_plan_summaries = StructuredTool.from_function(
    coroutine=_get_plan_summaries,
    name="get_plan_summaries",
    description="读取指定城市工作台所有版本的修改摘要（修改原因）。"
)

#读取最新短期记忆
async def _get_latest_plan_summary(runtime:ToolRuntime)-> str:
    """
    读取最新短期记忆

    Args:
        runtime:工具的运行时
    Return:返回操作结果
    """
    try:
        #获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("读取短期记忆： 缺少用户 ID")
            return "错误：缺少用户 ID"
        #构建查询语句并执行
        stmt = select(Travel_Plan_Workspace.summary).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.end_time > func.now()).order_by(Travel_Plan_Workspace.start_time.desc()).limit(1)
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            result = result.scalar()
        if result:
            return result
        else:
            return "暂未保存短期记忆"
    except Exception as e:
        return tool_error_handler(e)
get_latest_plan_summary = StructuredTool.from_function(
    coroutine=_get_latest_plan_summary,
    name="get_latest_plan_summary",
    description="读取最新短期记忆"
)

#保存最终版
async def _confirm_plan_version(runtime:ToolRuntime,city:str,version_no:int) -> str:
    """
    保存最终版，删除工作表计划并将城市添加到冷区/刷新冷区时间

    Args:
        runtime:工具的运行时
        city:城市
        version_no:版本号
    Return:返回操作结果
    """
    try:
        version_no = int(version_no)
        #获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        #构建工作台计划表查询语句
        stmt_itinerary = select(Travel_Plan_Workspace.itinerary).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.city == city,Travel_Plan_Workspace.version_no == version_no)
        #构建工作台计划表对应的summary查询语句
        stmt_summary = select(Travel_Plan_Workspace.summary).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.city == city,Travel_Plan_Workspace.version_no <= version_no).order_by(Travel_Plan_Workspace.version_no.asc())
        #构建工作台计划表删除语句
        stmt_delete = delete(Travel_Plan_Workspace).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.city == city)
        #构建城市冷区计划表插入语句（这里‘覆写’主键同时表自带触发器（时间））
        stmt_insert_Cold_Cities = pg_insert(Cold_Zone_Cities).values(city=city,user_id=user_id).on_conflict_do_update(index_elements=[Cold_Zone_Cities.user_id,Cold_Zone_Cities.city],set_={"start_time":func.now(),})
        #执行 先查询计划表是否存在，插入计划表，删除工作台计划表，插入/刷新冷区
        async with SessionLocal.begin() as session:
            itinerary = await session.execute(stmt_itinerary)
            itinerary = itinerary.scalar()
            if itinerary is None:
                return "没有该版本的计划表"
            summary = await session.execute(stmt_summary)
            summary = summary.scalars().all()
            summary = json.dumps(summary, ensure_ascii=False)
            #构建最终计划表插入语句
            stmt_insert = insert(Travel_Plans).values(city=city,itinerary=itinerary,user_id=user_id,summary=summary)
            await session.execute(stmt_insert)
            await session.execute(stmt_delete)
            await session.execute(stmt_insert_Cold_Cities)
            #构建城市冷区景点计划表插入语句（这里‘覆写’主键同时表自带触发器（时间））
            plan_data:Dict[str,Any] = json.loads(itinerary)
            attractions = list(dict.fromkeys(act['place'].strip() for day in plan_data.get('days',[]) for act in day.get('activities',[]) if isinstance(act.get('place'),str) and act['place'].strip()))
            for attraction in attractions:
                stmt_insert_Cold_Zone_Attractions = pg_insert(Cold_zone_attractions).values(city=city,user_id=user_id,attraction=attraction).on_conflict_do_update(index_elements=[Cold_zone_attractions.user_id,Cold_zone_attractions.city,Cold_zone_attractions.attraction],set_={"start_time":func.now(),})
                await session.execute(stmt_insert_Cold_Zone_Attractions)
        return "添加成功"
    except Exception as e:
        return tool_error_handler(e)
confirm_plan_version = StructuredTool.from_function(
    coroutine=_confirm_plan_version,
    name="confirm_plan_version",
    description="保存最终版，删除工作表计划并将城市添加到冷区/刷新冷区时间,建议先调用list_plan_versions工具确认用户具体选择的计划表，再拿对应版本号"
)

#读取工作台指定城市计划列表
async def _list_plan_versions(runtime: ToolRuntime, city: str) -> str:
    """
    读取工作台指定城市所有计划表

    Args:
        runtime:工具的运行时
        city:城市名称
    Return:返回操作结果
    """
    try:
        #获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("读取计划表列表： 缺少用户 ID")
            return "错误：缺少用户 ID"
        #构建查询语句并执行
        stmt = select(Travel_Plan_Workspace.itinerary,Travel_Plan_Workspace.version_no).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.city == city,Travel_Plan_Workspace.end_time > func.now()).order_by(Travel_Plan_Workspace.version_no.asc())
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            rows = result.all()
        if not rows:
            return "没有旅行计划"
        lines = [
            f'{version}: {itinerary}' for itinerary, version in rows
        ]
        return '\n'.join(lines)
    except Exception as e:
        return tool_error_handler(e)
list_plan_versions = StructuredTool.from_function(
    coroutine=_list_plan_versions,
    name="list_plan_versions",
    description="读取工作台指定城市计划表列表（版本号：计划表）"
)

#查询工作台最新计划
async def _get_latest_workspace_plan(runtime:ToolRuntime,details:bool = False) -> str:
    """
    获取工作台最近修改的一版计划（不限城市），可选返回详情

    Args:
        runtime:工具的运行时
        details:可选：是否返回计划详情。默认为 False。
    """
    try:
        #获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        #构建查询语句并执行
        stmt = select(Travel_Plan_Workspace.itinerary, Travel_Plan_Workspace.version_no,Travel_Plan_Workspace.city).where(
            Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.end_time > func.now(),
        ).order_by(Travel_Plan_Workspace.start_time.desc()).limit(1)
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            rows = result.first()
        if not rows:
            return "没有旅行计划"
        #解包 计划表 版本号 城市
        itinerary, version_no, city = rows
        if details is not False:
            return f'城市{city}旅行计划：{itinerary}版本号：{version_no}'
        else:
            return f'城市{city}版本号：{version_no}'
    except Exception as e:
        return tool_error_handler(e)
get_latest_workspace_plan = StructuredTool.from_function(
    coroutine=_get_latest_workspace_plan,
    name="get_latest_workspace_plan",
    description="获取工作台最近修改的一版计划（不限城市），可选返回详情（城市 旅行计划 版本号）"
)

#查询用户工作台全部旅行计划(编号)
async def _list_workspace_plans(runtime:ToolRuntime) -> str:
    """
    查询用户工作台全部旅行计划的编号

    Args:
        runtime:工具的运行时
    Return:返回操作结果
    """
    try:
        #获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        #构建查询语句并执行
        stmt = select(Travel_Plan_Workspace.version_no,Travel_Plan_Workspace.city).where(Travel_Plan_Workspace.user_id == user_id,Travel_Plan_Workspace.end_time > func.now())
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            rows = result.all()
        if not rows:
            return "没有旅行计划"
        lines = [
            f'{version}: {city}' for version,city in rows
        ]
        return '\n'.join(lines)
    except Exception as e:
        return tool_error_handler(e)
list_workspace_plans = StructuredTool.from_function(
    coroutine=_list_workspace_plans,
    name="list_workspace_plans",
    description="查询用户工作台全部旅行计划编号"
)

list_plan_ids_by_city = StructuredTool.from_function(
    coroutine=_list_plan_ids_by_city,
    name="list_plan_ids_by_city",
    description="根据城市名称查询该城市的所有plan_id"
)

get_all_plan_id = StructuredTool.from_function(
    coroutine=_get_all_plan_id,
    name="get_all_plan_id",
    description="查询用户所有旅行地的plan_id"
)