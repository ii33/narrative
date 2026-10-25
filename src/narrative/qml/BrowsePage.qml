import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 数据浏览：左侧分类，中间节点列表（编号即阅读顺序），右侧详情 + 属性。
Item {
    id: page
    objectName: "browsePage"
    property var app
    readonly property var t: app.theme

    property var allNodes: []
    property string selectedType: ""
    property string query: ""
    property string selectedId: ""
    property var detail: ({})
    property var counts: ({})
    property int revision: 0
    property int total: 0

    readonly property var typeLabels: ({
        person: "人物", group: "群体", item: "物品",
        location: "地点", event: "事件", scene: "场景", storyline: "故事线", other: "其他"
    })
    readonly property var typeKeys: ["person", "group", "item", "location", "event", "scene", "storyline", "other"]

    // 「最新」+ 每个事件；编号即阅读顺序，所以顺序就是读者读到的顺序。
    readonly property var cutLabels: {
        var labels = ["最新（全部披露）"]
        for (var i = 0; i < eventOptions.length; ++i)
            labels.push(eventOptions[i].id + "  " + eventOptions[i].name)
        return labels
    }

    // 属性名由模型给出（identity / height / state…），这里只补常见的中文说法。
    readonly property var propertyLabels: ({
        identity: "身份", state: "状态", status: "状况", holder: "持有者",
        height: "身高", age: "年龄", appearance: "外貌", attire: "衣着",
        clothing: "衣着", skin: "肤色", face: "面容", eyes: "眼睛",
        belief: "认知", knowledge: "知识", plan: "打算", location: "位置",
        color: "颜色", function: "功能", content: "内容", definition: "定义",
        nature: "性质", type: "类型", reputation: "名声", relation: "关系",
        quality: "品质", realm: "境界", cultivation: "修为", deed: "行径",
        injury: "伤势", effect: "效果", equipment: "装备", protection: "防护",
        spending: "花费", education: "学历", inscription: "题字",
        seclusion_duration: "闭关时长"
    })
    readonly property var scopeLabels: ({world: "世界", cognition: "认知", claim: "声称"})

    ListModel { id: rows }
    ListModel { id: categories }

    // Plain JS arrays, not ListModel: a record carries nested arrays (related
    // nodes) and ListModel drops those on the way in.
    property var propertyRows: []      // 扁平列表，「全部」视图用
    property var categoryGroups: []    // 按大类分组
    property var inboundRows: []       // 别的节点指向本节点的记录
    property var vocabulary: []        // 当前节点类型的分类表
    property bool groupByCategory: true
    property var expandedProps: ({})
    property var pickerTargets: []
    property var eventOptions: []
    property var mergeNames: []
    // 阅读位置：选一个事件，属性区显示"读到这里时"的状态；空串表示看到最新。
    property string cutEventId: ""
    property string cutEventName: ""
    property string cutSourceId: ""
    property int cutStart: -1
    property int cutLater: 0
    // 节点列表是否跟着阅读位置收窄。默认全部显示。
    property bool timeFilter: false

    function load() {
        var spec = {view: "table", limit: 2000}
        // 只有「按时间显示」才把节点列表也切到阅读位置；属性区始终跟着切点。
        if (page.timeFilter && page.cutSourceId.length > 0 && page.cutStart >= 0)
            spec.disclosure_cut = {source_id: page.cutSourceId, start: page.cutStart}
        bridge.loadGraphView(spec)
    }

    function nameOf(id) {
        for (var i = 0; i < allNodes.length; ++i)
            if (allNodes[i].id === id) return allNodes[i].name
        return id
    }

    function applyNodes(list) {
        allNodes = list || []
        var tally = {}
        for (var k = 0; k < page.typeKeys.length; ++k)
            tally[page.typeKeys[k]] = 0
        var events = []
        for (var i = 0; i < allNodes.length; ++i) {
            var kind = allNodes[i].node_type || "other"
            tally[kind] = (tally[kind] || 0) + 1
            if (kind === "event") events.push({id: allNodes[i].id, name: allNodes[i].name || allNodes[i].id})
        }
        // 事件列表只从「全部显示」的那次结果里取：收窄后的列表只剩已出场的节点，
        // 拿它当选项会让人无法再往前走。
        if (!page.timeFilter) eventOptions = events
        counts = tally
        rebuildCategories()
        rebuild()
        // 收窄后选中的节点可能还没出场：不能留着一个"此刻还不存在"的详情。
        if (page.selectedId.length > 0 && !allNodes.some(function (node) {
            return node.id === page.selectedId
        })) {
            page.selectedId = ""
            page.detail = ({})
            page.propertyRows = []
            page.categoryGroups = []
            page.inboundRows = []
            page.expandedProps = ({})
        }
    }

    function rebuildCategories() {
        categories.clear()
        categories.append({key: "", label: "全部", count: allNodes.length})
        for (var i = 0; i < page.typeKeys.length; ++i) {
            var key = page.typeKeys[i]
            categories.append({key: key, label: page.typeLabels[key], count: counts[key] || 0})
        }
    }

    function matches(node) {
        if (page.selectedType.length > 0 && (node.node_type || "other") !== page.selectedType)
            return false
        var needle = page.query.trim().toLowerCase()
        if (needle.length === 0) return true
        if (String(node.name || "").toLowerCase().indexOf(needle) >= 0) return true
        var aliases = node.aliases || []
        for (var i = 0; i < aliases.length; ++i)
            if (String(aliases[i]).toLowerCase().indexOf(needle) >= 0) return true
        return false
    }

    function rebuild() {
        rows.clear()
        var shown = 0
        for (var i = 0; i < allNodes.length; ++i) {
            var node = allNodes[i]
            if (!page.matches(node)) continue
            shown += 1
            rows.append({
                id: node.id || "",
                name: node.name || "未命名",
                kindLabel: page.typeLabels[node.node_type] || node.node_type || "节点",
                note: node.identity_note || ""
            })
        }
        total = shown
    }

    function pickType(kind) {
        selectedType = kind
        rebuild()
    }

    // 事件按编号排列（编号即阅读顺序），所以下拉里的事件顺序就是读者读到的顺序。
    function pickCut(index) {
        // 让下拉显示和实际切点始终一致：按钮和外部调用都走这里。
        if (cutBox.currentIndex !== index) cutBox.currentIndex = index
        var event = index > 0 ? page.eventOptions[index - 1] : null
        cutEventId = event ? event.id : ""
        cutEventName = event ? event.name : ""
        cutLater = 0
        // 切点的来源位置由后端解析，列表与属性共用同一个答案。
        bridge.setReadingCut(cutEventId)
    }

    function toggleTimeFilter() {
        page.timeFilter = !page.timeFilter
        page.load()
    }

    function backToLatest() {
        cutBox.currentIndex = 0
        page.pickCut(0)
    }

    // 上一个 / 下一个时间点。下拉里 0 是「最新」，1..N 是事件，
    // 所以时间顺序是 1 → N → 最新(0)，两头各自接上。
    function stepCut(delta) {
        var total = page.eventOptions.length
        if (total === 0) return
        var index = cutBox.currentIndex
        if (delta < 0) {
            if (index === 0) index = total
            else if (index > 1) index -= 1
        } else {
            if (index === 0) return
            index = index < total ? index + 1 : 0
        }
        cutBox.currentIndex = index
        page.pickCut(index)
    }

    function select(id) {
        selectedId = id
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        expandedProps = ({})
        bridge.nodeDetail(id)
        bridge.loadNodeProperties(id)
    }

    function valueText(item) {
        if (item.retracted) return "（已撤回）"
        if (item.value_node) return item.value_node.name
        if (item.value === null || item.value === undefined) return "（空）"
        return String(item.value)
    }

    function buildItem(item) {
        var records = item.records || []
        var ids = []
        for (var i = 0; i < records.length; ++i) ids.push(records[i].id)
        var scopes = item.scopes || [item.scope || "world"]
        var scopeText = ""
        for (var s = 0; s < scopes.length; ++s) {
            scopeText += (s > 0 ? "/" : "")
                + (page.scopeLabels[scopes[s]] || scopes[s] || "世界")
        }
        return {
            // 同一方面可能分在两个大类里，key 要带分类，否则展开状态会串。
            key: (item.aspect || item.property_id) + "|" + (item.category || ""),
            // 中文名由后端给（property_labels），未知属性名回退到原名。
            label: item.label || page.propertyLabels[item.property_id] || item.property_id || "",
            names: (item.property_ids || []).length > 1 ? item.property_ids.join(" / ") : "",
            nameIds: item.property_ids || [],
            category: item.category || "其他",
            categorySource: item.category_source || "",
            otherCategories: item.other_categories || [],
            recordIds: ids,
            valueText: page.valueText(item),
            valueNodeId: item.value_node ? item.value_node.id : "",
            scopeText: scopes.length > 1 ? scopeText : (page.scopeLabels[item.scope] || "世界"),
            mixedScope: scopes.length > 1,
            holderId: item.holder_id || "",
            holderName: item.holder_name
                || (item.holder_id ? page.nameOf(item.holder_id) : ""),
            validFrom: item.current_retracted ? "" : ("起于 " + (item.current_from_event || "—")),
            historyText: item.history_count > 0 ? "另有 " + item.history_count + " 条历史" : "",
            laterText: (item.later_count || 0) > 0
                ? "此后还有 " + item.later_count + " 条披露" : "",
            records: records
        }
    }

    function applyProperties(payload) {
        var groups = payload.categories || []
        var built = []
        var all = []
        for (var g = 0; g < groups.length; ++g) {
            var items = []
            var source = groups[g].items || []
            for (var i = 0; i < source.length; ++i) {
                var row = page.buildItem(source[i])
                items.push(row)
                all.push(row)
            }
            built.push({
                key: groups[g].key, label: groups[g].key,
                count: groups[g].count || items.length, items: items
            })
        }
        vocabulary = payload.vocabulary || []
        inboundRows = payload.inbound || []
        cutLater = payload.later_count || 0
        if (payload.cut) cutEventName = page.nameOf(String(payload.cut))
        categoryGroups = built
        propertyRows = all
        expandedProps = ({})
    }

    // 移动分类：只改整理位置，取值和披露顺序都不动。
    function askCategory(current, targets) {
        if (!targets || targets.length === 0) return
        pickerTargets = targets
        categoryPicker.current = current
        categoryPicker.open()
    }

    function applyCategory(category) {
        if (pickerTargets.length === 0) return
        bridge.categorizeProperties(JSON.stringify(pickerTargets), category)
    }

    // 属性归并：一行里出现多个名字时，选一个保留，其余按它读。
    // 归并和移动分类是两件事：一个改含义，一个只改位置。
    function askMerge(names) {
        if (!names || names.length < 2) return
        mergeNames = names
        mergeDialog.open()
    }

    function applyMerge(target) {
        for (var i = 0; i < mergeNames.length; ++i)
            if (mergeNames[i] !== target) bridge.mergeProperties(mergeNames[i], target)
        mergeDialog.close()
    }

    function openSource(sourceId, start) {
        app.openSource(sourceId, start)
    }

    function openCategorySettings() {
        categoryEditor.text = page.vocabulary.join("\n")
        categoryDialog.open()
    }

    // 补录：人工写一条披露，仍然先成为候选。
    function openAddProperty() {
        addPropertyField.text = ""
        addValueField.text = ""
        addQuoteField.text = ""
        addCategoryField.currentIndex = Math.max(0, page.vocabulary.indexOf("其他"))
        addPropertyDialog.open()
    }

    function submitAddProperty() {
        var event = page.eventOptions[addEventField.currentIndex] || ({})
        var node = page.detail.node || ({})
        var sourceId = node.evidence ? node.evidence.source_id
            : (node.disclosure ? node.disclosure.source_id : "")
        bridge.addPropertyRecord({
            node_id: page.selectedId,
            property_id: addPropertyField.text,
            value: addValueField.text,
            event_id: event.id || "",
            category: addCategoryField.currentText,
            source: addSourceField.currentText,
            quote: addQuoteField.text,
            source_id: sourceId || ""
        })
        addPropertyDialog.close()
    }

    function saveCategorySettings() {
        var lines = categoryEditor.text.split("\n")
        var names = []
        for (var i = 0; i < lines.length; ++i) {
            var name = lines[i].trim()
            if (name.length > 0 && name !== "其他" && names.indexOf(name) < 0) names.push(name)
        }
        if (names.length === 0) {
            app.toast("至少要保留一个大类", true)
            return
        }
        var config = {}
        config[detail.node ? detail.node.node_type : "other"] = names
        bridge.savePropertyCategories(JSON.stringify(config))
        categoryDialog.close()
    }

    function jumpTo(id) {
        if (!id) return
        // A node may be filtered out of the list; clear the filter first so the
        // jump always lands somewhere visible.
        if (page.selectedType.length > 0 || page.query.length > 0) {
            page.selectedType = ""
            page.query = ""
            searchField.text = ""
            page.rebuild()
        }
        page.select(id)
        for (var i = 0; i < rows.count; ++i) {
            if (rows.get(i).id === id) {
                nodeList.positionViewAtIndex(i, ListView.CenterInView)
                return
            }
        }
    }

    function toggleProperty(key) {
        var next = ({})
        for (var name in expandedProps)
            next[name] = expandedProps[name]
        next[key] = !next[key]
        expandedProps = next
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.load()

    Connections {
        target: app
        function onModeChanged() {
            if (app.mode === "workspace") {
                page.selectedId = ""
                page.detail = ({})
                page.selectedType = ""
                page.query = ""
                searchField.text = ""
                propertyRows = []
                categoryGroups = []
                inboundRows = []
                expandedProps = ({})
                page.cutEventId = ""
                page.cutEventName = ""
                page.cutLater = 0
                page.load()
            }
        }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "graph_view") {
                var canvas = payload.canvas || ({})
                revision = canvas.revision || revision
                page.applyNodes(canvas.nodes)
            } else if (kind === "detail") {
                detail = payload
                var index = payload.node ? page.typeKeys.indexOf(payload.node.node_type) : -1
                kindEditor.currentIndex = index >= 0 ? index : 0
            } else if (kind === "reading_cut") {
                page.cutSourceId = payload.source_id || ""
                page.cutStart = payload.start === undefined ? -1 : payload.start
                page.cutEventName = payload.name || ""
                page.cutLater = 0
                // 列表（按时间显示时）和属性都按这个位置重取。
                page.load()
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "node_properties") {
                page.applyProperties(payload)
            } else if (kind === "property_categorize") {
                app.toast("已移到「" + (payload.category || "") + "」", false)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "property_merge") {
                app.toast("已合并到「" + (payload.target_id || "") + "」", false)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "property_add") {
                if (payload.status === "pending") {
                    app.toast("已提交补录，去「审核」接受后生效", false)
                } else {
                    var first = (payload.diagnostics || [])[0] || ({})
                    app.toast("补录未通过预检：" + (first.message || "未知原因"), true)
                }
            } else if (kind === "property_categories") {
                var nodeType = page.detail.node ? page.detail.node.node_type : "other"
                page.vocabulary = (payload.vocabulary || ({}) )[nodeType] || page.vocabulary
                if (payload.saved) {
                    app.toast("分类表已保存", false)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                }
            } else if (kind === "node_edit") {
                app.toast("已提交修订，等待审核", false)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: searchField
                objectName: "browseSearchField"
                Layout.preferredWidth: 300
                placeholderText: "搜索名称或别名"
                onTextChanged: { page.query = text; page.rebuild() }
            }
            Label {
                text: total + " 个节点 · 修订 " + revision
                color: t.muted
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
            // 列表是否跟着阅读位置收窄。
            Button {
                objectName: "browseTimeFilter"
                text: page.timeFilter ? "按时间显示" : "全部显示"
                flat: !page.timeFilter
                highlighted: page.timeFilter
                font.pixelSize: 11
                onClicked: page.toggleTimeFilter()
            }
            // 阅读位置：切到某个事件，右侧属性显示"读到这里时"的状态。
            Label {
                text: "阅读位置"
                color: t.muted
                font.pixelSize: 12
            }
            Button {
                objectName: "browseCutPrev"
                text: "‹"
                flat: true
                font.pixelSize: 15
                enabled: page.eventOptions.length > 0
                    && (cutBox.currentIndex > 1 || cutBox.currentIndex === 0)
                onClicked: page.stepCut(-1)
            }
            ComboBox {
                id: cutBox
                objectName: "browseCutBox"
                Layout.preferredWidth: 280
                model: page.cutLabels
                enabled: page.eventOptions.length > 0
                onActivated: page.pickCut(currentIndex)
            }
            Button {
                objectName: "browseCutNext"
                text: "›"
                flat: true
                font.pixelSize: 15
                enabled: page.eventOptions.length > 0 && cutBox.currentIndex !== 0
                onClicked: page.stepCut(1)
            }
            Button {
                objectName: "browseCutReset"
                text: "最新"
                flat: true
                enabled: page.cutEventId.length > 0
                onClicked: page.backToLatest()
            }
            Button {
                text: "刷新"
                flat: true
                onClicked: page.load()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // -- categories ---------------------------------------------
            Rectangle {
                Layout.preferredWidth: 190
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 2

                    Label {
                        text: "分类"
                        color: t.muted
                        font.pixelSize: 11
                        Layout.bottomMargin: 4
                    }

                    Repeater {
                        model: categories

                        delegate: Item {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34

                            Rectangle {
                                anchors.fill: parent
                                radius: 5
                                color: page.selectedType === modelData.key ? t.accentSoft : "transparent"
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 6
                                Label {
                                    text: modelData.label
                                    color: page.selectedType === modelData.key ? t.accent : t.ink
                                    font.pixelSize: 13
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: modelData.count
                                    color: modelData.count > 0 ? t.muted : t.line
                                    font.pixelSize: 11
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: page.pickType(modelData.key)
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // -- node list ----------------------------------------------
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    id: nodeList
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: rows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string name
                        required property string kindLabel

                        width: ListView.view.width
                        height: 42
                        radius: 4
                        color: page.selectedId === id ? t.accentSoft : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label {
                                text: id
                                color: t.muted
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: kindLabel
                                color: t.accent
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: name
                                color: page.selectedId === id ? t.accent : t.ink
                                font.pixelSize: 13
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.select(id)
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: rows.count === 0
                        text: page.allNodes.length === 0 ? "这个工程还没有节点。" : "该分类下没有节点。"
                        color: t.muted
                    }
                }
            }

            // -- detail --------------------------------------------------
            Rectangle {
                Layout.preferredWidth: 440
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    Label {
                        text: detail.node ? detail.node.name : "未选择节点"
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        color: t.ink
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    Label {
                        Layout.fillWidth: true
                        text: detail.node
                            ? "编号 " + detail.node.id + " · "
                              + (page.typeLabels[detail.node.node_type] || detail.node.node_type)
                            : "在中间选择一个节点"
                        color: t.muted
                        font.pixelSize: 12
                    }

                    ScrollView {
                        id: detailScroll
                        objectName: "browseDetailScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        ColumnLayout {
                            width: detailScroll.availableWidth
                            spacing: 8

                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && (detail.node.aliases || []).length > 0
                                    ? "别名：" + detail.node.aliases.join("、") : ""
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && detail.node.identity_note ? detail.node.identity_note : ""
                                color: t.body
                                font.pixelSize: 13
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && detail.node.evidence
                                    ? "原文：" + detail.node.evidence.quote : ""
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: text.length > 0
                                text: detail.node && detail.node.disclosure
                                    ? "披露位置：" + detail.node.disclosure.start : ""
                                color: t.muted
                                font.pixelSize: 11
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                Layout.topMargin: 4
                                color: t.line
                            }

                            // 阅读位置生效时说明清楚：这是"读到这里"的状态，不是全部。
                            Label {
                                Layout.fillWidth: true
                                Layout.topMargin: 2
                                visible: page.cutEventId.length > 0
                                wrapMode: Text.Wrap
                                text: "读到「" + (page.cutEventName || page.cutEventId) + "」时的状态"
                                    + (cutBox.currentIndex > 0
                                        ? "（第 " + cutBox.currentIndex + " / "
                                            + page.eventOptions.length + " 个事件）" : "")
                                    + (page.cutLater > 0
                                        ? "；此后还有 " + page.cutLater + " 条披露未显示" : "")
                                color: t.warn
                                font.pixelSize: 11
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Label {
                                    text: "属性"
                                    color: t.ink
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    text: propertyRows.length + " 项"
                                    color: t.muted
                                    font.pixelSize: 11
                                }
                                Item { Layout.fillWidth: true }
                                Button {
                                    objectName: "categoryModeButton"
                                    text: page.groupByCategory ? "按大类" : "全部"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: page.groupByCategory = !page.groupByCategory
                                }
                                Button {
                                    objectName: "categorySettingsButton"
                                    text: "分类表"
                                    flat: true
                                    font.pixelSize: 10
                                    enabled: detail.node !== undefined && detail.node !== null
                                    onClicked: page.openCategorySettings()
                                }
                                Button {
                                    objectName: "addPropertyButton"
                                    text: "补录"
                                    flat: true
                                    font.pixelSize: 10
                                    enabled: page.selectedId.length > 0 && page.eventOptions.length > 0
                                    onClicked: page.openAddProperty()
                                }
                            }

                            // 按大类：一个大类一行标题，下面是它底下的属性。
                            // 分类只是整理位置，取值和披露顺序都不受影响。
                            Repeater {
                                objectName: "browseCategoryGroups"
                                model: page.groupByCategory ? page.categoryGroups : []

                                delegate: Column {
                                    required property var modelData

                                    Layout.fillWidth: true
                                    Layout.topMargin: 2
                                    spacing: 0
                                    visible: modelData.items.length > 0

                                    RowLayout {
                                        width: parent.width
                                        spacing: 6
                                        Label {
                                            text: modelData.label
                                            color: t.muted
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                        }
                                        Label {
                                            text: modelData.count
                                            color: t.muted
                                            font.pixelSize: 10
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 1
                                            color: t.line
                                        }
                                    }
                                    Repeater {
                                        model: modelData.items

                                        delegate: PropertyItem {
                                            required property var modelData
                                            width: parent.width
                                            row: modelData
                                            host: page
                                        }
                                    }
                                }
                            }

                            // 全部：不分组，按属性名排。
                            Repeater {
                                model: page.groupByCategory ? [] : page.propertyRows

                                delegate: PropertyItem {
                                    required property var modelData
                                    width: parent.width
                                    row: modelData
                                    host: page
                                }
                            }

                            // 反向引用：别的节点上的记录指向本节点。同一份记录，
                            // 第二个入口，不复制、不改披露位置。
                            Column {
                                Layout.fillWidth: true
                                Layout.topMargin: 6
                                spacing: 2
                                visible: inboundRows.length > 0

                                RowLayout {
                                    width: parent.width
                                    spacing: 6
                                    Label {
                                        text: "反向引用"
                                        color: t.muted
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: inboundRows.length
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 1
                                        color: t.line
                                    }
                                }
                                Repeater {
                                    objectName: "browseInboundRows"
                                    model: inboundRows

                                    delegate: RowLayout {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 6

                                        Label {
                                            text: modelData.category
                                            color: t.muted
                                            font.pixelSize: 10
                                            Layout.preferredWidth: 76
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            text: modelData.node_name
                                            color: t.accent
                                            font.pixelSize: 11
                                            font.underline: true
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: page.jumpTo(modelData.node_id)
                                            }
                                        }
                                        Label {
                                            text: "· " + (page.propertyLabels[modelData.property_id]
                                                || modelData.property_id) + " = "
                                                + (modelData.node_type ? "" : "")
                                                + page.nameOf(modelData.value)
                                            color: t.body
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: t.line
                    }

                    Label {
                        text: "节点类型"
                        color: t.muted
                        font.pixelSize: 11
                    }
                    ComboBox {
                        id: kindEditor
                        Layout.fillWidth: true
                        model: page.typeKeys.map(function (key) { return page.typeLabels[key] })
                        enabled: detail.node !== undefined && detail.node !== null
                    }
                    Button {
                        Layout.fillWidth: true
                        text: "提交类型修订"
                        enabled: page.selectedId.length > 0
                        onClicked: bridge.reviseNode(page.selectedId,
                            {node_type: page.typeKeys[kindEditor.currentIndex]})
                    }
                    Button {
                        Layout.fillWidth: true
                        text: "撤回该节点"
                        enabled: page.selectedId.length > 0
                        onClicked: bridge.reviseNode(page.selectedId, {retracted: true})
                    }
                }
            }
        }
    }

    // 移动分类：只改整理位置。取值、披露顺序、原文引文都不动。
    Dialog {
        id: categoryPicker
        objectName: "categoryPicker"
        property string current: ""
        title: "移到哪个大类"
        modal: true
        anchors.centerIn: parent
        width: 320

        contentItem: ColumnLayout {
            spacing: 4
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "分类只决定显示位置，不改取值，也不改披露顺序。"
                color: t.muted
                font.pixelSize: 11
            }
            Repeater {
                model: page.vocabulary
                delegate: Button {
                    required property string modelData
                    Layout.fillWidth: true
                    flat: true
                    highlighted: modelData === categoryPicker.current
                    text: modelData + (modelData === categoryPicker.current ? "  ·当前" : "")
                    onClicked: {
                        page.applyCategory(modelData)
                        categoryPicker.close()
                    }
                }
            }
            Button {
                Layout.fillWidth: true
                flat: true
                text: "取消"
                onClicked: categoryPicker.close()
            }
        }
    }

    // 归并属性名：同一个方面用了两个名字时，选一个保留。
    Dialog {
        id: mergeDialog
        objectName: "mergeDialog"
        title: "合并成一个名字"
        modal: true
        anchors.centerIn: parent
        width: 340

        contentItem: ColumnLayout {
            spacing: 4
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "选一个保留的名字。其余名字的记录照旧保存，只是按这个名字读；"
                    + "撤回这次合并就恢复成两个名字。"
                color: t.muted
                font.pixelSize: 11
            }
            Repeater {
                model: page.mergeNames
                delegate: Button {
                    required property string modelData
                    Layout.fillWidth: true
                    flat: true
                    text: modelData
                    onClicked: page.applyMerge(modelData)
                }
            }
            Button {
                Layout.fillWidth: true
                flat: true
                text: "取消"
                onClicked: mergeDialog.close()
            }
        }
    }

    // 分类表：每行一个大类，顺序即显示顺序；「其他」固定在最后。
    Dialog {
        id: categoryDialog
        objectName: "categoryDialog"
        title: "分类表 · " + (detail.node
            ? (page.typeLabels[detail.node.node_type] || detail.node.node_type) : "")
        modal: true
        anchors.centerIn: parent
        width: 380

        contentItem: ColumnLayout {
            spacing: 6
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "每行一个大类，顺序即显示顺序。「其他」始终在最后，不用写。"
                color: t.muted
                font.pixelSize: 11
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 220
                TextArea {
                    id: categoryEditor
                    objectName: "categoryEditor"
                    wrapMode: TextArea.NoWrap
                    font.pixelSize: 13
                    selectByMouse: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: categoryDialog.close()
                }
                Button {
                    objectName: "categorySaveButton"
                    text: "保存"
                    highlighted: true
                    onClicked: page.saveCategorySettings()
                }
            }
        }
    }

    // 补录：人工写一条披露。它仍然先成为候选，接受后才进正式资料。
    Dialog {
        id: addPropertyDialog
        objectName: "addPropertyDialog"
        title: "补一条属性"
        modal: true
        anchors.centerIn: parent
        width: 460

        contentItem: ColumnLayout {
            spacing: 6

            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "补录也会先成为候选，到「审核」接受后才进正式资料。引文逐字复制，程序会核对。"
                color: t.muted
                font.pixelSize: 11
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 8
                rowSpacing: 6

                Label { text: "属性名"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addPropertyField
                    objectName: "addPropertyField"
                    Layout.fillWidth: true
                    placeholderText: "例如 state"
                }
                Label { text: "值"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addValueField
                    objectName: "addValueField"
                    Layout.fillWidth: true
                    placeholderText: "照原文写"
                }
                Label { text: "披露事件"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addEventField
                    objectName: "addEventField"
                    Layout.fillWidth: true
                    model: page.eventOptions.map(function (item) { return item.name })
                }
                Label { text: "大类"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addCategoryField
                    objectName: "addCategoryField"
                    Layout.fillWidth: true
                    model: page.vocabulary
                }
                Label { text: "来源"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addSourceField
                    objectName: "addSourceField"
                    Layout.fillWidth: true
                    model: ["narration", "speech", "thought", "observation", "document"]
                }
                Label { text: "原文引文"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addQuoteField
                    objectName: "addQuoteField"
                    Layout.fillWidth: true
                    placeholderText: "逐字复制；可留空，但补上才能回查"
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: addPropertyDialog.close()
                }
                Button {
                    objectName: "addPropertySubmit"
                    text: "提交补录"
                    highlighted: true
                    onClicked: page.submitAddProperty()
                }
            }
        }
    }
}
