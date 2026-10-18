import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Fusion as Fusion

Rectangle {
    id: scaleView
    required property var controller
    required property var groupData
    required property string axisId
    property string selectedEventId: ""
    signal eventSelected(string eventId)
    readonly property bool unplaced: groupData.kind === "unknown"
    readonly property bool relative: groupData.kind === "relative"
    readonly property var entries: groupData.entries || []
    readonly property real low: groupData.low !== undefined ? groupData.low : (entries.length ? Math.min.apply(null, entries.map(function(e) { return e.low })) : 0)
    readonly property real high: groupData.high !== undefined ? groupData.high : (entries.length ? Math.max.apply(null, entries.map(function(e) { return e.high })) : 0)
    readonly property real span: Math.max(1, high-low)
    readonly property real cardWidth: 164
    readonly property real canvasWidth: Math.max(scroller.width, unplaced ? entries.length*176+24 : Math.max(entries.length*92+184, relative ? (high-low)*176+184 : 0))
    readonly property var layoutRows: assignRows()
    readonly property int cardRows: Math.max(1, layoutRows.reduce(function(maximum,row) { return Math.max(maximum,row+1) },0))
    readonly property real axisY: 24+cardRows*48
    implicitHeight: axisY + (unplaced ? 12 : 36) + ((groupData.relation_labels || []).length ? 22 : 0)
    color: "#ffffff"; radius: 8; border.color: "#dce4ef"
    clip: true

    function position(value) { return 92+(value-low)/span * Math.max(20,canvasWidth-184) }
    function entryX(index) { return unplaced ? 12+index*176 : Math.max(8,Math.min(position(entries[index].anchor)-cardWidth/2,canvasWidth-cardWidth-8)) }
    function assignRows() {
        var ends=[], rows=[]
        for (var i=0; i<entries.length; ++i) {
            var x=entryX(i), row=0
            while (row<ends.length && ends[row]>x) row++
            ends[row]=x+cardWidth+8
            rows.push(row)
        }
        return rows
    }
    function ticks() {
        if (unplaced) return []
        if (relative) { var values=[]; for (var i=low; i<=high; ++i) values.push(i); return values }
        if (low===high) return [low]
        return [low,low+span/4,low+span/2,low+span*3/4,high]
    }

    Label { x: 10; y: 5; width: parent.width-20; text: scaleView.groupData.label; color: "#60738d"; font.pixelSize: 10; elide: Text.ElideRight }
    Flickable {
        id: scroller
        x: 0; y: 21; width: parent.width; height: parent.height-21
        contentWidth: scaleView.canvasWidth; contentHeight: height
        clip: true; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.horizontal: ScrollBar { policy: scroller.contentWidth>scroller.width ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
        Item {
            width: scaleView.canvasWidth; height: scroller.height
            Rectangle { visible: !scaleView.unplaced; x: 20; y: scaleView.axisY-21; width: parent.width-40; height: 2; color: "#607da9" }
            Repeater {
                model: scaleView.ticks()
                delegate: Item {
                    required property real modelData
                    x: scaleView.position(modelData); y: scaleView.axisY-26
                    Rectangle { width: 1; height: 10; color: "#607da9" }
                    Label { x: -76; y: 12; width: 152; horizontalAlignment: Text.AlignHCenter; text: scaleView.relative ? String(modelData) : scaleView.controller.coordinateText(modelData,scaleView.groupData.form); color: "#60738d"; font.pixelSize: 10 }
                }
            }
            Repeater {
                model: scaleView.entries
                delegate: Item {
                    id: entryItem
                    required property var modelData
                    required property int index
                    x: scaleView.entryX(index); y: scaleView.layoutRows[index]*48+3
                    width: scaleView.cardWidth; height: 44
                    readonly property real anchorX: scaleView.position(modelData.anchor)-x
                    Rectangle { visible: !scaleView.relative && entryItem.modelData.range === true; x: scaleView.position(entryItem.modelData.low)-entryItem.x; y: scaleView.axisY-24-entryItem.y; width: scaleView.position(entryItem.modelData.high)-scaleView.position(entryItem.modelData.low); height: 6; radius: 3; color: "#95b1e1" }
                    Rectangle { visible: !scaleView.unplaced; x: entryItem.anchorX; y: 43; width: 1; height: Math.max(1,scaleView.axisY-21-entryItem.y-43); color: "#c3d2e9" }
                    Rectangle { visible: !scaleView.unplaced; x: entryItem.anchorX-4; y: scaleView.axisY-25-entryItem.y; width: 9; height: 9; radius: 5; color: scaleView.selectedEventId===entryItem.modelData.event.id ? "#2859c5" : "#7d9ccb" }
                    Fusion.Button {
                        objectName: "timelineEvent_" + scaleView.axisId + "_" + entryItem.modelData.event.id
                        property string eventId: entryItem.modelData.event.id
                        width: parent.width; height: 44
                        highlighted: scaleView.selectedEventId===eventId
                        text: entryItem.modelData.event.name + (entryItem.modelData.event.child_axis_id ? "  ▴" : "")
                        contentItem: Column {
                            Text { width: parent.width; text: parent.parent.text; color: "#172b4d"; font.pixelSize: 12; font.bold: scaleView.selectedEventId===entryItem.modelData.event.id; elide: Text.ElideRight }
                            Text { width: parent.width; text: entryItem.modelData.caption; color: "#60738d"; font.pixelSize: 9; elide: Text.ElideRight }
                        }
                        onClicked: scaleView.eventSelected(eventId)
                        ToolTip.visible: hovered
                        ToolTip.text: entryItem.modelData.event.name+"\n"+entryItem.modelData.caption
                    }
                }
            }
        }
    }
    Label { visible: (scaleView.groupData.relation_labels || []).length>0; x: 10; y: parent.height-20; width: parent.width-20; text: (scaleView.groupData.relation_labels || []).join("；"); color: "#8657b6"; font.pixelSize: 10; elide: Text.ElideRight; ToolTip.visible: relationHover.hovered; ToolTip.text: text
        HoverHandler { id: relationHover }
    }
}
