import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: layerView
    required property var controller
    required property var layerData
    required property int level
    property string selectedEventId: ""
    signal eventSelected(string eventId)
    signal closeRequested()
    objectName: "nestedAxis_" + (layerData.id || "root")
    implicitHeight: 200
    color: level === 0 ? "#f0f5ff" : "#f7f9fc"
    border.color: level === 0 ? "#91ade1" : "#d7e0ec"; radius: 10

    function laneCaption(event,lane) {
        var occurrence = event.occurrence || ({})
        function describe(ref) {
            if (ref.timeline_id === lane.timeline_id && ref.form === lane.form) return controller.boundaryLabel(ref)
            if (ref.form === "event_boundary") return "参照" + controller.eventName(ref.event_id) + controller.boundaryLabel(ref.boundary)
            return "另属" + (ref.form === "datetime" ? "日期时间轴" : "数值时间轴")
        }
        if (occurrence.kind === "point") return describe(occurrence.point)
        return ["start","end"].map(function(key) {
            var bound = occurrence[key] || ({})
            return controller.boundaryLabel(key) + (bound.state === "known" ? " " + describe(bound.ref) : "未明")
        }).join(" · ")
    }

    function groups() {
        var result = [], placed = ({})
        var lanes = layerData.lanes || []
        for (var i=0; i<lanes.length; ++i) {
            var lane = lanes[i], entries = []
            for (var j=0; j<lane.events.length; ++j) {
                var event = lane.events[j]
                if (!controller.nodeShown(event.id,"event")) continue
                var refs = event.coordinate_refs.filter(function(ref) { return ref.timeline_id === lane.timeline_id && ref.form === lane.form })
                var values = refs.map(function(ref) { return controller.coordinate(ref) })
                if (!values.length) continue
                placed[event.id] = true
                entries.push({event:event,low:Math.min.apply(null,values),high:Math.max.apply(null,values),anchor:(Math.min.apply(null,values)+Math.max.apply(null,values))/2,range:values.length>1,caption:laneCaption(event,lane)})
            }
            entries.sort(function(a,b) { return a.low-b.low || a.event.id.localeCompare(b.event.id) })
            if (entries.length) result.push({kind:"known",form:lane.form,label:"已知时间 · " + lane.label + " · " + (lane.form === "number" ? "真实数值刻度" : "日期时间"),entries:entries})
        }
        var axes = layerData.relative_axes || []
        for (var a=0; a<axes.length; ++a) {
            var axis = axes[a], byEvent = ({}), relativeEntries = []
            for (var p=0; p<(axis.points || []).length; ++p) {
                var point = axis.points[p]
                if (!controller.nodeShown(point.event_id,"event")) continue
                if (!byEvent[point.event_id]) byEvent[point.event_id] = []
                byEvent[point.event_id].push(point)
            }
            for (var e=0; e<(layerData.events || []).length; ++e) {
                var relativeEvent = layerData.events[e], points = byEvent[relativeEvent.id]
                if (!points || placed[relativeEvent.id]) continue
                var ordinals = points.map(function(point) { return point.ordinal })
                var anchor = points.find(function(point) { return point.boundary === "start" || point.boundary === "point" }) || points[0]
                placed[relativeEvent.id] = true
                relativeEntries.push({event:relativeEvent,low:Math.min.apply(null,ordinals),high:Math.max.apply(null,ordinals),anchor:anchor.ordinal,caption:"边界参考："+points.map(function(point) { return controller.boundaryLabel(point.boundary)+" @"+point.ordinal }).join(" · ")})
            }
            relativeEntries.sort(function(a,b) { return a.anchor-b.anchor || a.event.id.localeCompare(b.event.id) })
            var labels = (layerData.relations || []).filter(function(relation) { return (axis.explicit_relation_ids || []).indexOf(relation.id)>=0 }).map(function(relation) { return controller.eventName(relation.subject_id)+controller.boundaryLabel(relation.temporal_left)+" "+controller.temporalLabel(relation.temporal_relation)+" → "+controller.eventName(relation.object_id)+controller.boundaryLabel(relation.temporal_right) })
            var allOrdinals = (axis.points || []).map(function(point) { return point.ordinal })
            if (relativeEntries.length) result.push({kind:"relative",low:Math.min.apply(null,allOrdinals),high:Math.max.apply(null,allOrdinals),label:"相对组 " + (a+1) + " · 仅原断言定先后，刻度非时长且未必可比",entries:relativeEntries,relation_labels:labels})
        }
        var unknown = (layerData.events || []).filter(function(event) { return !placed[event.id] && controller.nodeShown(event.id,"event") }).map(function(event) { return {event:event,low:0,high:0,anchor:0,caption:"时间未明 · 排列不代表先后"} })
        if (unknown.length) result.push({kind:"unknown",label:"时间未明 · 无时间刻度",entries:unknown})
        return result
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 10; spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: layerView.level === 0 ? "总览 · 关键事件" : "第 " + (layerView.level+1) + " 层 · " + layerView.controller.eventName(layerView.layerData.owner_event_id) + " 的子事件"; font.bold: true; color: "#172b4d"; elide: Text.ElideRight }
            Button { objectName: "closeAxis_" + layerView.layerData.id; visible: layerView.level > 0; text: "收起此层"; flat: true; onClicked: layerView.closeRequested() }
        }
        Label { visible: (layerView.layerData.cross_level_relation_count || 0) > 0; Layout.fillWidth: true; text: "另有 " + (layerView.layerData.cross_level_relation_count || 0) + " 条跨层时间关系，见时间关系依据"; color: "#60738d"; font.pixelSize: 10 }
        ScrollView {
            id: layerScroll
            objectName: "axisScroll_" + layerView.layerData.id
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentWidth: availableWidth
            Column {
                width: layerScroll.availableWidth; spacing: 8
                Repeater {
                    model: layerView.groups()
                    TimelineEventScale {
                        required property var modelData
                        width: parent.width
                        controller: layerView.controller; groupData: modelData; axisId: layerView.layerData.id
                        selectedEventId: layerView.selectedEventId
                        onEventSelected: function(eventId) { layerView.eventSelected(eventId) }
                    }
                }
                Label { width: parent.width; visible: layerView.groups().length === 0; text: "没有符合筛选的事件"; color: "#60738d"; padding: 12 }
            }
        }
    }
}
