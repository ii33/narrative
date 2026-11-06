import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id:usage
    property var statistics:({})
    property var t
    property bool expanded:false
    readonly property var context:statistics.context||({})
    readonly property bool known:context.input_tokens!==null&&context.input_tokens!==undefined
    readonly property bool capacityKnown:context.context_window!==null&&context.context_window!==undefined&&context.context_window>0
    readonly property real fraction:known&&capacityKnown?context.input_tokens/context.context_window:0
    function number(value){return value===null||value===undefined?"未知":Number(value).toLocaleString(Qt.locale(),"f",0)}
    function metric(value,key){value=value||{};var exact=value[key],subtotal=(value.known_totals||{})[key];return exact!==null&&exact!==undefined?number(exact):subtotal!==null&&subtotal!==undefined?"至少 "+number(subtotal):"未知"}
    function usageLine(value){return "输入 "+metric(value,"input_tokens")+" · 输出 "+metric(value,"output_tokens")+" · 合计 "+metric(value,"total_tokens")+" · 请求 "+metric(value,"requests")}
    function cacheLine(value){
        value=value||{};
        var rate=value.cache_hit_rate,knownRate=value.known_cache_hit_rate;
        var exact=rate!==null&&rate!==undefined;
        var partial=!exact&&knownRate!==null&&knownRate!==undefined;
        return "缓存命中 "+metric(value,"cache_read_tokens")+" Token · "+(exact?(Number(rate)*100).toFixed(1)+"%":partial?(Number(knownRate)*100).toFixed(1)+"%（已知请求）":"命中率未知");
    }
    function cacheCoverage(value){
        value=value||{};
        return "覆盖 "+number(value.cache_covered_requests)+" 个请求 · 未知 "+number(value.cache_unknown_requests)+" 个请求";
    }
    function sampleTime(value){if(!value)return "";var date=new Date(value);return isNaN(date.getTime())?"时间未知":Qt.formatDateTime(date,"yyyy-MM-dd HH:mm:ss")}
    spacing:3
    RowLayout {
        Layout.fillWidth:true;spacing:4
        Label{objectName:"assistantContextOccupancy";text:"上下文 "+usage.number(usage.context.input_tokens)+" / "+usage.number(usage.context.context_window)+(usage.known&&usage.capacityKnown?" · "+(usage.fraction*100).toFixed(1)+"%":"");font.pixelSize:11;Layout.fillWidth:true;elide:Text.ElideRight;ToolTip.visible:contextHover.hovered;ToolTip.text:text;HoverHandler{id:contextHover}}
        Label{objectName:"assistantContextSource";text:usage.context.source==="actual"?"实测":usage.context.source==="estimated"?"估计":"未知";font.pixelSize:10;color:usage.t.muted}
        Label{visible:usage.context.stale===true;text:"较早记录";font.pixelSize:10;color:usage.t.muted}
        ToolButton{objectName:"assistantUsageToggle";text:usage.expanded?"收起":"用量";font.pixelSize:10;onClicked:usage.expanded=!usage.expanded}
    }
    Label{objectName:"assistantLastKnownContext";visible:!usage.known&&usage.context.last_known_input_tokens!==null&&usage.context.last_known_input_tokens!==undefined;text:"最近实测 "+usage.number(usage.context.last_known_input_tokens)+"（当前占用未知）";font.pixelSize:10;color:usage.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
    Label{objectName:"assistantChatCacheHeadline";text:"聊天 "+usage.cacheLine(usage.statistics.chat_usage);font.pixelSize:10;color:usage.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
    ProgressBar{objectName:"assistantContextBar";Layout.fillWidth:true;Layout.preferredHeight:4;from:0;to:1;value:Math.min(1,usage.fraction);opacity:usage.known&&usage.capacityKnown?1:0}
    Label{visible:usage.context.compaction_pending===true;text:"上下文正在压缩，等待下一次占用记录。";font.pixelSize:10;color:usage.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
    ColumnLayout{visible:usage.expanded;Layout.fillWidth:true;spacing:3
        Label{objectName:"assistantTaskUsage";text:"本任务："+usage.usageLine(usage.statistics.task_usage);font.pixelSize:10;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{objectName:"assistantChatUsage";text:"当前聊天："+usage.usageLine(usage.statistics.chat_usage);font.pixelSize:10;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{objectName:"assistantTaskCacheUsage";text:"本任务 "+usage.cacheLine(usage.statistics.task_usage)+"\n"+usage.cacheCoverage(usage.statistics.task_usage)+" · 缓存写入 "+usage.metric(usage.statistics.task_usage,"cache_write_tokens");font.pixelSize:10;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{objectName:"assistantChatCacheUsage";text:"当前聊天 "+usage.cacheLine(usage.statistics.chat_usage)+"\n"+usage.cacheCoverage(usage.statistics.chat_usage)+" · 缓存写入 "+usage.metric(usage.statistics.chat_usage,"cache_write_tokens");font.pixelSize:10;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{visible:(usage.statistics.chat_usage||{}).incomplete===true||(usage.statistics.task_usage||{}).incomplete===true;text:"部分请求未提供完整用量，未知项未按零计入。";font.pixelSize:10;color:usage.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{text:"以 Token 计数。输入已包含缓存用量，不重复相加。用量为累计记录；上方上下文仅为最近一次占用。";font.pixelSize:10;color:usage.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{objectName:"assistantContextDetails";text:"压缩阈值 "+usage.number(usage.context.compression_threshold)+" · 输出预留 "+usage.number(usage.context.output_reserve)+(usage.context.sampled_at?"\n记录时间 "+usage.sampleTime(usage.context.sampled_at):"");font.pixelSize:10;color:usage.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
    }
}
