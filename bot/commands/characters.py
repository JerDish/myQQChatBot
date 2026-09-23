"""内置二次元女角色清单。

为什么内置而不是爬百科：
    萌娘百科的图片有防盗链（无条件 403），API 又会被 Cloudflare 人机验证拦，
    完全不适合做实时查询。所以这里内置一份可控的清单，
    图片另外从图库接口按角色名搜（见 sources.fetch_character_image）。

格式：(角色名, 作品, 一句话简介)
偏重 galgame、二游、日漫。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Sequence

from ..log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class CharacterEntry:
    name: str
    work: str
    desc: str
    # 图库搜索用关键词（留空则用 name）
    keyword: str = ""

    @property
    def search_key(self) -> str:
        return self.keyword or self.name


# 作品名尽量用通用叫法，方便图库匹配
CHARACTERS: Sequence[CharacterEntry] = (
    # ---------------- galgame ----------------
    CharacterEntry("二阶堂真红", "五彩斑斓的世界", "赋予悠马魔法的「半透明的魔法使」。外表年幼却沉稳，爱喝不加糖的黑咖啡。", "真紅"),
    CharacterEntry("鹿野上悠马", "五彩斑斓的世界", "古书店的店主，真红的契约者。", "鹿ノ上悠馬"),
    CharacterEntry("观月栞", "五彩斑斓的世界", "开朗活泼的学妹，真红线的重要角色。", "観月栞"),
    CharacterEntry("如月澪", "五彩斑斓的世界", "冷漠毒舌，实则温柔的青梅竹马。", "如月澪"),
    CharacterEntry("加奈", "五彩斑斓的世界", "元气满满的妹妹角色。", "加奈"),
    CharacterEntry("雪之下雪乃", "我的青春恋爱物语果然有问题", "总武高中二年级，成绩顶尖、性格孤高的完美主义者。", "雪ノ下雪乃"),
    CharacterEntry("由比滨结衣", "我的青春恋爱物语果然有问题", "气氛制造者，擅长察言观色的温柔少女。", "由比ヶ浜結衣"),
    CharacterEntry("一色彩羽", "我的青春恋爱物语果然有问题", "后辈，表面乖巧实则精明。", "一色いろは"),
    CharacterEntry("千反田爱瑠", "冰菓", "好奇心旺盛的大小姐，「我很好奇！」", "千反田える"),
    CharacterEntry("五更琉璃", "我的妹妹哪有这么可爱", "中二病黑猫，穿哥特萝莉装，其实很害羞。", "五更瑠璃"),
    CharacterEntry("高坂桐乃", "我的妹妹哪有这么可爱", "模特兼宅女，嘴硬心软的妹妹。", "高坂桐乃"),
    CharacterEntry("新垣绫濑", "我的妹妹哪有这么可爱", "桐乃的闺蜜，认真且有点病娇。", "新垣あやせ"),
    CharacterEntry("间桐樱", "Fate/stay night", "温柔坚韧的后辈，擅长料理。", "間桐桜"),
    CharacterEntry("远坂凛", "Fate/stay night", "魔术名门继承人，傲娇又可靠。", "遠坂凛"),
    CharacterEntry("Saber", "Fate/stay night", "亚瑟王，认真严肃的骑士王。", "セイバー Fate"),
    CharacterEntry("Saber Alter", "Fate/stay night", "被黑化的亚瑟王，冷酷而强大。", "セイバーオルタ"),
    CharacterEntry("贞德", "Fate/Grand Order", "裁定者，圣女般的存在。", "ジャンヌ・ダルク"),
    CharacterEntry("玛修·基列莱特", "Fate/Grand Order", "盾兵，温柔坚毅的后辈。", "マシュ・キリエライト"),
    CharacterEntry("斯卡哈", "Fate/Grand Order", "影之国女王，枪术之师。", "スカサハ"),
    CharacterEntry("尼禄", "Fate/EXTRA", "罗马暴君，自称「皇帝」。", "ネロ・クラウディウス"),
    CharacterEntry("式部茉优", "苍之彼方的四重奏", "冷静沉着的射击部部长。", "式部茉優"),
    CharacterEntry("仓科明日香", "苍之彼方的四重奏", "阳光开朗，热爱飞翔的少女。", "倉科明日香"),
    CharacterEntry("有坂真白", "苍之彼方的四重奏", "活泼的运动系后辈。", "有坂真白"),
    CharacterEntry("泽渡美玲", "苍之彼方的四重奏", "FC 部的可靠前辈，性格爽朗，是大家信赖的存在。", "沢渡美玲"),
    CharacterEntry("篝之雾枝", "千恋万花", "冷淡的美少女，实则内心细腻。", "篝ノ霧枝"),
    CharacterEntry("丛雨", "千恋万花", "自称神明的巫女。", "叢雨"),
    CharacterEntry("常陆茉子", "千恋万花", "温柔的青梅竹马。", "常陸茉子"),
    CharacterEntry("蕾娜", "千恋万花", "来自异国的武士少女。", "レナ 千恋万花"),
    CharacterEntry("四季夏目", "ATRI", "冷静理性的机器人少女。", "アトリ"),
    CharacterEntry("小鞠", "ATRI", "天真烂漫的小女孩。", "小鞠 ATRI"),
    CharacterEntry("白羽苏芳", "FLOWERS", "沉静的少女，爱好读书。", "白羽蘇芳"),
    CharacterEntry("匂坂真由理", "FLOWERS", "苏芳的挚友，温柔体贴，擅长倾听。", "匂坂マユリ"),
    CharacterEntry("八重垣艾莉丝", "FLOWERS", "聪明冷静的转学生。", "八重垣えりす"),
    CharacterEntry("绫地宁宁", "千之刃涛", "开朗的千金小姐。", "綾地寧々"),
    CharacterEntry("因幡巡", "千之刃涛", "认真负责的班长。", "因幡巡"),
    CharacterEntry("椎名真昼", "关于邻家的天使大人", "被称为「天使大人」的完美邻居。", "椎名真昼"),
    CharacterEntry("樱岛麻衣", "青春猪头少年", "童星出身，理性冷静的学姐。", "桜島麻衣"),
    CharacterEntry("牧之原翔子", "青春猪头少年", "温柔体贴的后辈。", "牧之原翔子"),
    CharacterEntry("古河渚", "CLANNAD", "温柔而坚强的少女。", "古河渚"),
    CharacterEntry("藤林杏", "CLANNAD", "双胞胎中的姐姐，性格直率，擅长运动，对妹妹极为爱护。", "藤林杏"),
    CharacterEntry("一之濑琴美", "CLANNAD", "天才少女，爱好小提琴。", "一ノ瀬ことみ"),
    CharacterEntry("坂上智代", "CLANNAD", "学生会长，武力值极高。", "坂上智代"),
    CharacterEntry("伊吹风子", "CLANNAD", "海星雕刻的少女。", "伊吹風子"),
    CharacterEntry("泉此方", "幸运星", "御宅族少女，身高 142cm。", "泉こなた"),
    CharacterEntry("柊镜", "幸运星", "傲娇的吐槽担当。", "柊かがみ"),
    CharacterEntry("柊司", "幸运星", "天然呆的双胞胎妹妹。", "柊つかさ"),
    CharacterEntry("高良美雪", "幸运星", "成绩优秀的温柔少女，嗜好读书，对朋友照顾有加。", "高良みゆき"),
    CharacterEntry("凉宫春日", "凉宫春日的忧郁", "唯恐天下不乱的 SOS 团团长。", "涼宮ハルヒ"),
    CharacterEntry("长门有希", "凉宫春日的忧郁", "沉默的文艺部员，真实身份是外星人接口。", "長門有希"),
    CharacterEntry("朝比奈实玖瑠", "凉宫春日的忧郁", "来自未来的少女。", "朝比奈みくる"),
    CharacterEntry("蕾姆", "Re:从零开始的异世界生活", "蓝发双子妹妹，极度忠诚。", "レム"),
    CharacterEntry("拉姆", "Re:从零开始的异世界生活", "罗兹瓦尔宅邸的女仆，毒舌但内心温柔，对妹妹蕾姆极为爱护。", "ラム"),
    CharacterEntry("艾米莉娅", "Re:从零开始的异世界生活", "银发半精灵，善良的候补王。", "エミリア"),
    CharacterEntry("贝蒂", "Re:从零开始的异世界生活", "禁书库的守护者。", "ベアトリス(リゼロ)"),
    CharacterEntry("赫萝", "狼与香辛料", "自称「贤狼」的丰收之神。", "ホロ"),
    CharacterEntry("千石抚子", "化物语", "内向的少女，后来成为蛇神。", "千石撫子"),
    CharacterEntry("战场原黑仪", "化物语", "毒舌的班长，文具是武器。", "戦場ヶ原ひたぎ"),
    CharacterEntry("八九寺真宵", "化物语", "迷路的蜗牛少女。", "八九寺真宵"),
    CharacterEntry("羽川翼", "化物语", "成绩优秀的班长。", "羽川翼"),
    CharacterEntry("忍野忍", "化物语", "金发吸血鬼萝莉。", "忍野忍"),
    CharacterEntry("桐谷直叶", "刀剑神域", "剑道少女，桐人的表妹。", "桐ヶ谷直葉"),
    CharacterEntry("亚丝娜", "刀剑神域", "闪光，料理达人的剑士。", "アスナ"),
    CharacterEntry("朝田诗乃", "刀剑神域", "狙击手，克服了枪械恐惧。", "朝田詩乃"),
    CharacterEntry("绫波丽", "新世纪福音战士", "第一适格者，沉默寡言。", "綾波レイ"),
    CharacterEntry("明日香", "新世纪福音战士", "第二适格者，自尊心强。", "アスカ"),
    CharacterEntry("葛城美里", "新世纪福音战士", "NERV 作战部长。", "葛城ミサト"),
    CharacterEntry("晓美焰", "魔法少女小圆", "时间回溯的魔法少女，为救小圆轮回无数次。", "暁美ほむら"),
    CharacterEntry("鹿目圆", "魔法少女小圆", "温柔善良的主角。", "鹿目まどか"),
    CharacterEntry("巴麻美", "魔法少女小圆", "经验丰富的魔法少女前辈，举止优雅，喜欢招待后辈喝茶。", "巴マミ"),
    CharacterEntry("美树沙耶香", "魔法少女小圆", "正义感强烈的少女。", "美樹さやか"),
    CharacterEntry("佐仓杏子", "魔法少女小圆", "独来独往的魔法少女。", "佐倉杏子"),
    CharacterEntry("阿尔托莉雅", "Fate 系列", "骑士王，恪守骑士道。", "アルトリア"),
    # ---------------- 二游 ----------------
    CharacterEntry("刻晴", "原神", "璃月七星之「玉衡星」，雷元素。", "刻晴"),
    CharacterEntry("甘雨", "原神", "半仙之兽，璃月七星的秘书。", "甘雨"),
    CharacterEntry("胡桃", "原神", "往生堂七十七代堂主，性格跳脱。", "胡桃"),
    CharacterEntry("雷电将军", "原神", "稻妻的雷神，追求永恒。", "雷電将軍"),
    CharacterEntry("八重神子", "原神", "鸣神大社宫司，狐狸。", "八重神子"),
    CharacterEntry("神里绫华", "原神", "社奉行神里家大小姐，白鹭公主。", "神里綾華"),
    CharacterEntry("宵宫", "原神", "长野原烟花店的看板娘。", "宵宮"),
    CharacterEntry("纳西妲", "原神", "须弥的草神，智慧之神。", "ナヒーダ"),
    CharacterEntry("芙宁娜", "原神", "枫丹的水神，热爱戏剧与表演，举止浮夸却藏着深深的孤独。", "フリーナ"),
    CharacterEntry("荧", "原神", "来自异世界的旅行者。", "蛍"),
    CharacterEntry("三月七", "崩坏：星穹铁道", "星穹列车的乘客，失忆的少女。", "三月なのか"),
    CharacterEntry("姬子", "崩坏：星穹铁道", "星穹列车的领航员。", "姫子"),
    CharacterEntry("布洛妮娅", "崩坏：星穹铁道", "贝洛伯格的大守护者。", "ブローニャ"),
    CharacterEntry("希儿", "崩坏：星穹铁道", "贝洛伯格地下区的战力担当。", "ゼーレ"),
    CharacterEntry("镜流", "崩坏：星穹铁道", "云上五骁之一，剑术冠绝仙舟，被称为剑首。", "鏡流"),
    CharacterEntry("花火", "崩坏：星穹铁道", "假面愚者的一员。", "花火"),
    CharacterEntry("符玄", "崩坏：星穹铁道", "太卜司的太卜，擅长占卜与推演，做事一丝不苟。", "符玄"),
    CharacterEntry("流萤", "崩坏：星穹铁道", "格拉默铁骑的成员。", "ホタル"),
    CharacterEntry("爱莉希雅", "崩坏3", "逐火十三英桀的第二位。", "エリシア"),
    CharacterEntry("琪亚娜", "崩坏3", "女武神，性格开朗活泼，是队伍里的气氛担当。", "キアナ"),
    CharacterEntry("雷电芽衣", "崩坏3", "御三家之一，冷静可靠，剑术出众，是队伍的核心。", "雷電芽衣"),
    CharacterEntry("芙乐艾", "崩坏3", "沉稳冷静的少女，思维缜密，擅长分析与策划。", "フカ"),
    CharacterEntry("星野日向", "碧蓝档案", "阿拜多斯的学生会长。", "ホシノ ブルアカ"),
    CharacterEntry("砂狼白子", "碧蓝档案", "阿拜多斯的成员。", "シロコ"),
    CharacterEntry("小鸟游星野", "碧蓝档案", "阿拜多斯对策委员会的成员，看似懒散实则可靠。", "ホシノ"),
    CharacterEntry("阿罗娜", "碧蓝档案", "夏莱的 AI 助手，性格天真烂漫，总是元气满满。", "アロナ"),
    CharacterEntry("优香", "碧蓝档案", "千年科学学园的会计。", "ユウカ"),
    CharacterEntry("未花", "碧蓝档案", "三一综合学园的学生。", "ミカ"),
    CharacterEntry("日奈", "碧蓝档案", "格黑娜学园的风纪委员长。", "ヒナ"),
    CharacterEntry("能代", "碧蓝航线", "重樱的轻巡洋舰，性格认真严谨，是可靠的战力。", "能代"),
    CharacterEntry("企业", "碧蓝航线", "白鹰的航空母舰。", "エンタープライズ"),
    CharacterEntry("贝尔法斯特", "碧蓝航线", "皇家轻巡，女仆长。", "ベルファスト"),
    CharacterEntry("独角兽", "碧蓝航线", "皇家轻母，性格温和害羞，对指挥官十分依恋。", "ユニコーン"),
    CharacterEntry("碧蓝航线:果敢", "碧蓝航线", "维希教廷的驱逐舰。", "ル・ファンタスク"),
    CharacterEntry("德克萨斯", "明日方舟", "企鹅物流的干员。", "テキサス"),
    CharacterEntry("能天使", "明日方舟", "企鹅物流的干员，性格开朗。", "エクシア"),
    CharacterEntry("陈", "明日方舟", "龙门近卫局特别督察组警司，剑术高超，责任感极强。", "チェン"),
    CharacterEntry("斯卡蒂", "明日方舟", "深海猎人，沉默寡言却实力强大，背负着深海的秘密。", "スカジ"),
    CharacterEntry("凯尔希", "明日方舟", "罗德岛的医疗主管，冷静理性，掌握着大量不为人知的真相。", "ケルシー"),
    CharacterEntry("阿米娅", "明日方舟", "罗德岛的公开领袖，虽然年幼却承担着沉重的责任。", "アーミヤ"),
    CharacterEntry("W", "明日方舟", "萨卡兹佣兵，性格乖张爱玩，实则心思深沉。", "W アークナイツ"),
    CharacterEntry("泥岩", "明日方舟", "萨卡兹佣兵，沉稳可靠，沉默地守护着同伴。", "マッドロック"),
    CharacterEntry("薇尔莉特", "紫罗兰永恒花园", "自动手记人偶，寻找「我爱你」的含义。", "ヴァイオレット"),
    CharacterEntry("时崎狂三", "约会大作战", "最恶的精灵，操纵时间。", "時崎狂三"),
    CharacterEntry("夜刀神十香", "约会大作战", "剑之精灵，性格直率单纯，食量惊人。", "夜刀神十香"),
    CharacterEntry("诱宵美九", "约会大作战", "歌之精灵，以歌声为武器，自称要守护所有女性。", "誘宵美九"),
    CharacterEntry("鸢一折纸", "约会大作战", "认真严谨的精灵，表情少有变化，行动力极强。", "鳶一折紙"),
    CharacterEntry("四糸乃", "约会大作战", "冰之精灵，胆子小。", "四糸乃"),
    CharacterEntry("冬马和纱", "白色相簿2", "天才钢琴少女，性格孤高。", "冬馬かずさ"),
    CharacterEntry("小木曾雪菜", "白色相簿2", "歌姬，温柔而坚强。", "小木曽雪菜"),
    CharacterEntry("泽村英梨梨", "路人女主的养成方法", "同人画师，傲娇。", "澤村・スペンサー・英梨々"),
    CharacterEntry("加藤惠", "路人女主的养成方法", "存在感稀薄的女主角。", "加藤恵"),
    CharacterEntry("霞之丘诗羽", "路人女主的养成方法", "人气轻小说家学姐，毒舌且自信，笔下文字极为犀利。", "霞ヶ丘詩羽"),
    CharacterEntry("冰堂美智留", "路人女主的养成方法", "乐队主唱，性格开朗直率，行动力满点。", "氷堂美智留"),
    CharacterEntry("平泽唯", "轻音少女", "吉他手，天然呆。", "平沢唯"),
    CharacterEntry("秋山澪", "轻音少女", "贝斯手，容易害羞。", "秋山澪"),
    CharacterEntry("中野梓", "轻音少女", "轻音部的后辈吉他手，认真努力，容易被学姐们捉弄。", "中野梓"),
    CharacterEntry("田井中律", "轻音少女", "轻音部的鼓手兼部长，性格活泼爱闹，经常被澪吐槽。", "田井中律"),
    CharacterEntry("琴吹䌷", "轻音少女", "键盘手，大方的大小姐。", "琴吹紬"),
    CharacterEntry("白洲梓", "蔚蓝反射", "性格内向的少女，不擅表达，却有着坚定的内心。", "白洲アズサ"),
    CharacterEntry("小鸟游六花", "中二病也要谈恋爱", "自称「邪王真眼」持有者。", "小鳥遊六花"),
    CharacterEntry("丹生谷森夏", "中二病也要谈恋爱", "想要抹去黑历史的班长。", "丹生谷森夏"),
    CharacterEntry("凸守早苗", "中二病也要谈恋爱", "自称六花的侍从，配合演出中二设定，其实成绩优秀。", "凸守早苗"),
    CharacterEntry("香风智乃", "请问您今天要来点兔子吗", "Rabbit House 的看板娘，性格冷静沉稳，头上总顶着一只兔子。", "香風智乃"),
    CharacterEntry("保登心爱", "请问您今天要来点兔子吗", "开朗的姐姐角色。", "保登心愛"),
    CharacterEntry("天天座理世", "请问您今天要来点兔子吗", "军人气质的少女。", "天天座理世"),
    CharacterEntry("桐间纱路", "请问您今天要来点兔子吗", "努力打工的少女。", "桐間紗路"),
    CharacterEntry("宇治松千夜", "请问您今天要来点兔子吗", "和风咖啡店的看板娘。", "宇治松千夜"),
    CharacterEntry("阿库娅", "为美好的世界献上祝福", "水之女神，废物女神。", "アクア"),
    CharacterEntry("惠惠", "为美好的世界献上祝福", "红魔族少女，只会爆裂魔法，每天必放一发。", "めぐみん"),
    CharacterEntry("达克妮斯", "为美好的世界献上祝福", "女骑士，防御力极高却总是打不中，性格相当独特。", "ダクネス"),
    CharacterEntry("悠悠", "为美好的世界献上祝福", "红魔族少女，自称惠惠的对手，其实很怕寂寞。", "ゆんゆん"),
    CharacterEntry("伊莉雅", "Fate/kaleid liner", "魔法少女，爱因兹贝伦的少女。", "イリヤ"),
    CharacterEntry("美游", "Fate/kaleid liner", "另一条世界线的少女。", "美遊"),
    CharacterEntry("克洛伊", "Fate/kaleid liner", "伊莉雅的另一个人格。", "クロエ"),
    CharacterEntry("间桐樱 Alter", "Fate 系列", "被黑影吞噬后的樱，性格转为冷酷，力量强大。", "間桐桜 オルタ"),
    CharacterEntry("BB", "Fate/EXTRA CCC", "Moon Cell 的 AI。", "BB Fate"),
    CharacterEntry("阿尔托莉雅 Alter", "Fate 系列", "被圣杯诅咒的骑士王，以暴君之姿重现。", "アルトリア オルタ"),
    CharacterEntry("南小鸟", "LoveLive!", "μ's 的成员。", "南ことり"),
    CharacterEntry("高坂穗乃果", "LoveLive!", "μ's 的队长。", "高坂穂乃果"),
    CharacterEntry("园田海未", "LoveLive!", "μ's 的成员，认真。", "園田海未"),
    CharacterEntry("西木野真姬", "LoveLive!", "μ's 的作曲担当。", "西木野真姫"),
    CharacterEntry("矢泽妮可", "LoveLive!", "μ's 的成员，自称「妮可妮可妮」。", "矢澤にこ"),
    CharacterEntry("绚濑绘里", "LoveLive!", "μ's 的学生会长。", "絢瀬絵里"),
    CharacterEntry("东条希", "LoveLive!", "μ's 的副会长。", "東條希"),
    CharacterEntry("小泉花阳", "LoveLive!", "μ's 的成员，胆小。", "小泉花陽"),
    CharacterEntry("星空凛", "LoveLive!", "μ's 的成员，运动系。", "星空凛"),
)


# 按作品分组，方便「同作品再抽一次」之类的扩展（暂时没用上）
BY_WORK: dict = {}
for _c in CHARACTERS:
    BY_WORK.setdefault(_c.work, []).append(_c)


def random_character() -> CharacterEntry:
    return random.choice(list(CHARACTERS))


def total() -> int:
    return len(CHARACTERS)
