import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 数据浏览：左侧分类，中间节点列表（编号即阅读顺序），右侧详情 + 属性。
Item {
    id: page
    objectName: "browsePage"
    property var app
    readonly property var t: app.theme

    property var nodes: []
    property var steps: []
    property var events: []
    property var nameIndex: ({})
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

    // 「最新」+ 当前粒度的每一步；编号即阅读顺序，所以顺序就是读者读到的顺序。
    readonly property var cutLabels: {
        var labels = ["最新（全部披露）"]
        for (var i = 0; i < steps.length; ++i)
            labels.push(steps[i].id + "  " + steps[i].name)
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
    // 工程里一个节点都没有（区别于"被条件过滤掉了"）。
    property int projectTotal: 0
    readonly property bool isEmptyProject: projectTotal === 0

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
    property var mergeNames: []
    // 事件等级：场景 / 事件。默认场景（粒度更粗，一次读一段）。
    property string granularity: "scene"
    // 阅读位置：cutId 是场景或事件的编号，空串表示看到最新。
    property string cutId: ""
    property string cutEventId: ""
    property string cutName: ""
    property int cutLater: 0
    // 勾选后只列当前这一步的相关节点。
    property bool relatedOnly: false
    // 节点列表是否跟着阅读位置收窄。默认全部显示。
    property bool timeFilter: false
    // 属性区上一次用的切点：搜索时切点没变就不重载，展开状态得以保留。
    property string appliedCut: ""
    property string pendingFocus: ""

    // 查询、过滤、计数、切点解析都在后端：界面只提交条件并渲染结果。
    function load() {
        bridge.loadBrowse({
            granularity: page.granularity,
            cut_id: page.cutId,
            node_type: page.selectedType,
            query: page.query,
            related_only: page.relatedOnly,
            time_filter: page.timeFilter
        })
    }

    function nameOf(id) {
        var name = page.nameIndex[id]
        return name === undefined ? id : name
    }

    function applyBrowse(payload) {
        revision = payload.revision === undefined ? revision : payload.revision
        steps = payload.steps || []
        events = payload.events || []
        counts = payload.counts || ({})
        projectTotal = payload.project_total === undefined ? 0 : payload.project_total
        total = payload.total || 0
        nodes = payload.nodes || []
        var cut = payload.cut || null
        cutEventId = cut ? cut.event_id : ""
        cutName = cut ? cut.step_name : ""
        if (!cut) cutLater = 0

        var index = page.nameIndex
        for (var i = 0; i < nodes.length; ++i)
            if (nodes[i].id) index[nodes[i].id] = nodes[i].name || nodes[i].id
        page.nameIndex = index

        rows.clear()
        for (var j = 0; j < nodes.length; ++j) {
            var node = nodes[j]
            rows.append({
                id: node.id || "",
                name: node.name || "未命名",
                kindLabel: page.typeLabels[node.node_type] || node.node_type || "节点",
                note: node.identity_note || ""
            })
        }
        rebuildCategories()
        syncCutIndex()

        // 收窄或过滤后选中的节点可能不在列表里：不能留着一个"此刻还不存在"的详情。
        if (page.selectedId.length > 0 && !nodes.some(function (node) {
            return node.id === page.selectedId
        })) {
            page.clearSelection()
        }
        if (page.pendingFocus.length > 0) {
            for (var k = 0; k < rows.count; ++k) {
                if (rows.get(k).id === page.pendingFocus) {
                    nodeList.positionViewAtIndex(k, ListView.CenterInView)
                    break
                }
            }
            page.pendingFocus = ""
        }
        // 属性区跟同一个锚点事件走；切点没变就不重载。
        if (page.selectedId.length > 0 && page.appliedCut !== page.cutEventId) {
            page.appliedCut = page.cutEventId
            bridge.loadNodeProperties(page.selectedId)
        }
    }

    function rebuildCategories() {
        var all = 0
        for (var name in counts) all += counts[name]
        categories.clear()
        categories.append({key: "", label: "全部", count: all})
        for (var i = 0; i < page.typeKeys.length; ++i) {
            var key = page.typeKeys[i]
            categories.append({key: key, label: page.typeLabels[key], count: counts[key] || 0})
        }
    }

    function syncCutIndex() {
        var index = 0
        for (var i = 0; i < page.steps.length; ++i)
            if (page.steps[i].id === page.cutId) { index = i + 1; break }
        if (cutBox.currentIndex !== index) cutBox.currentIndex = index
    }

    function clearSelection() {
        selectedId = ""
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        expandedProps = ({})
        appliedCut = ""
    }

    function pickType(kind) {
        selectedType = kind
        page.load()
    }

    // 事件等级切换后原来的切点不再属于新的粒度，回到「最新」。
    function pickGranularity(value) {
        if (page.granularity === value) return
        page.granularity = value
        page.cutId = ""
        page.appliedCut = ""
        page.load()
    }

    // 步骤按编号排列（编号即阅读顺序），所以下拉里的顺序就是读者读到的顺序。
    function pickCut(index) {
        if (cutBox.currentIndex !== index) cutBox.currentIndex = index
        page.cutId = index > 0 ? page.steps[index - 1].id : ""
        page.cutLater = 0
        page.load()
    }

    function toggleTimeFilter() {
        page.timeFilter = !page.timeFilter
        page.load()
    }

    function toggleRelated() {
        page.relatedOnly = !page.relatedOnly
        page.load()
    }

    function backToLatest() {
        cutBox.currentIndex = 0
        page.pickCut(0)
    }

    // 上一个 / 下一个时间点。下拉里 0 是「最新」，1..N 是各步骤，
    // 所以时间顺序是 1 → N → 最新(0)，两头各自接上。
    function stepCut(delta) {
        var count = page.steps.length
        if (count === 0) return
        var index = cutBox.currentIndex
        if (delta < 0) {
            if (index === 0) index = count
            else if (index > 1) index -= 1
        } else {
            if (index === 0) return
            index = index < count ? index + 1 : 0
        }
        page.pickCut(index)
    }

    function select(id) {
        selectedId = id
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        expandedProps = ({})
        appliedCut = page.cutEventId
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
        var event = page.events[addEventField.currentIndex] || ({})
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
        // 目标节点可能被当前条件挡在列表外：先清掉条件，再跳过去。
        if (page.selectedType.length > 0 || page.query.length > 0 || page.relatedOnly) {
            page.selectedType = ""
            page.query = ""
            page.relatedOnly = false
            searchField.text = ""
        }
        page.pendingFocus = id
        page.load()
        page.select(id)
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
                page.cutId = ""
                page.cutEventId = ""
                page.cutName = ""
                page.cutLater = 0
                page.appliedCut = ""
                page.relatedOnly = false
                page.timeFilter = false
                page.granularity = "scene"
                page.load()
            }
        }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "browse") {
                page.applyBrowse(payload)
            } else if (kind === "detail") {
                detail = payload
            } else if (kind === "property_correct") {
                if (payload.status === "pending") {
                    app.toast("更正已提交，去「审核」接受后生效", false)
                } else {
                    var problem = (payload.diagnostics || [])[0] || ({})
                    app.toast("更正未通过预检：" + (problem.message || "未知原因"), true)
                }
            } else if (kind === "property_retract") {
                app.toast("已撤回 " + (payload.retracted || []).length + " 条记录", false)
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
                Layout.preferredWidth: 280
                placeholderText: "搜索名称或别名"
                // 输入停下来再查，避免每个字都跑一次后端。
                onTextChanged: { page.query = text; searchTimer.restart() }
            }
            Label {
                text: total + " 个节点 · 修订 " + revision
                color: t.muted
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "刷新"
                flat: true
                onClicked: page.load()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            // 事件等级：决定「阅读位置」按场景还是按事件走。
            Label {
                text: "事件等级"
                color: t.muted
                font.pixelSize: 12
            }
            Button {
                objectName: "browseGranularityScene"
                text: "场景"
                flat: page.granularity !== "scene"
                highlighted: page.granularity === "scene"
                font.pixelSize: 11
                onClicked: page.pickGranularity("scene")
            }
            Button {
                objectName: "browseGranularityEvent"
                text: "事件"
                flat: page.granularity !== "event"
                highlighted: page.granularity === "event"
                font.pixelSize: 11
                onClicked: page.pickGranularity("event")
            }
            // 列表是否跟着阅读位置收窄。
            Button {
                objectName: "browseTimeFilter"
                text: page.timeFilter ? "按时间显示" : "全部显示"
                flat: !page.timeFilter
                highlighted: page.timeFilter
                font.pixelSize: 11
                onClicked: page.toggleTimeFilter()
            }
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
                enabled: page.steps.length > 0
                    && (cutBox.currentIndex > 1 || cutBox.currentIndex === 0)
                onClicked: page.stepCut(-1)
            }
            ComboBox {
                id: cutBox
                objectName: "browseCutBox"
                Layout.fillWidth: true
                Layout.maximumWidth: 320
                model: page.cutLabels
                enabled: page.steps.length > 0
                onActivated: page.pickCut(currentIndex)
            }
            Button {
                objectName: "browseCutNext"
                text: "›"
                flat: true
                font.pixelSize: 15
                enabled: page.steps.length > 0 && cutBox.currentIndex !== 0
                onClicked: page.stepCut(1)
            }
            Button {
                objectName: "browseCutReset"
                text: "最新"
                flat: true
                enabled: page.cutEventId.length > 0
                onClicked: page.backToLatest()
            }
            CheckBox {
                id: relatedBox
                objectName: "browseRelatedOnly"
                text: "相关节点"
                checked: page.relatedOnly
                font.pixelSize: 11
                onToggled: page.toggleRelated()
            }
            Item { Layout.fillWidth: true }
        }

        // 搜索防抖：输入停下 200ms 才查。
        Timer {
            id: searchTimer
            interval: 200
            onTriggered: page.load()
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
                        text: page.isEmptyProject ? "这个工程还没有节点。" : "该分类下没有节点。"
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
                                text: "读到「" + (page.cutName || page.cutEventId) + "」时的状态"
                                    + (cutBox.currentIndex > 0
                                        ? "（第 " + cutBox.currentIndex + " / "
                                            + page.steps.length + " 个"
                                            + (page.granularity === "scene" ? "场景" : "事件")
                                            + "）" : "")
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
                                    enabled: page.selectedId.length > 0 && page.events.length > 0
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

                    // 节点字段、属性补录/更正/失效都在编辑器里，这里只留一个入口。
                    Button {
                        objectName: "browseOpenEditor"
                        Layout.fillWidth: true
                        text: "编辑节点"
                        enabled: page.selectedId.length > 0
                        onClicked: nodeEditor.openFor(page.selectedId)
                    }
                }
            }
        }
    }

    NodeEditor {
        id: nodeEditor
        host: page
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
                    model: page.events.map(function (item) { return item.name })
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
