// Test-only LD_PRELOAD library. It never attaches to or addresses another process.
#include <QAction>
#include <QApplication>
#include <QBackingStore>
#include <QCheckBox>
#include <QClipboard>
#include <QDialogButtonBox>
#include <QFile>
#include <QFileInfo>
#include <QImage>
#include <QLineEdit>
#include <QMainWindow>
#include <QMenu>
#include <QMenuBar>
#include <QMouseEvent>
#include <QPainter>
#include <QPointer>
#include <QProcessEnvironment>
#include <QPushButton>
#include <QRegularExpression>
#include <QSettings>
#include <QSignalSpy>
#include <QSplitter>
#include <QStyle>
#include <QStyleFactory>
#include <QTabBar>
#include <QTabWidget>
#include <QTest>
#include <QTimer>
#include <QToolBar>
#include <QWindow>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <unistd.h>

namespace {
QString root;
QFile report;
int failures = 0;

bool check(bool ok, const QString &message)
{
    const QByteArray line = ((ok ? "PASS " : "FAIL ") + message + '\n').toUtf8();
    report.write(line);
    report.flush();
    if (!ok) ++failures;
    return ok;
}

void note(const QString &message)
{
    report.write((message + '\n').toUtf8());
    report.flush();
}

QList<QWidget *> widgets(QWidget *parent, const char *type, bool visible = false)
{
    QList<QWidget *> result;
    for (auto *widget : parent->findChildren<QWidget *>()) {
        if (widget->inherits(type) && (!visible || widget->isVisible())) result.append(widget);
    }
    return result;
}

QAction *action(QMainWindow *window, const QString &name)
{
    // KXMLGUI action collections are not necessarily QObject children of the
    // window. Its action list contains the currently plugged-in session actions.
    for (auto *candidate : window->actions()) {
        if (candidate->objectName() == name) return candidate;
    }
    return nullptr;
}

bool trigger(QMainWindow *window, const QString &name)
{
    auto *found = action(window, name);
    if (!found) {
        QStringList names;
        for (auto *entry : window->actions()) names << entry->objectName();
        note("INFO window actions=" + names.join(','));
    }
    if (!check(found && found->isEnabled(), "action available: " + name)) return false;
    found->trigger();
    QTest::qWait(250);
    return true;
}

void chrome(QMainWindow *window, const QString &stage)
{
    const auto bars = window->findChildren<QToolBar *>();
    for (auto *bar : bars) {
        if (!bar->isHidden()) note("INFO visible toolbar at " + stage + ": " + bar->objectName());
    }
    check(!bars.isEmpty() && std::all_of(bars.begin(), bars.end(), [](auto *bar) { return bar->isHidden(); }),
          stage + ": native toolbars remain hidden");
    const auto headers = widgets(window, "Konsole::TerminalHeaderBar");
    check(!headers.isEmpty() && std::all_of(headers.begin(), headers.end(), [](auto *bar) { return bar->isHidden(); }),
          stage + ": all headers explicitly hidden, including inactive/nested panes");
}

void pixels(QMainWindow *window, const QList<QSplitter *> &splitters)
{
    window->repaint();
    QTest::qWait(1500); // Let the terminal's transient resize labels disappear.
    auto *store = window->backingStore();
    auto *device = store ? store->paintDevice() : nullptr;
    // Read the actual raster backing store, not QWidget::render with artificial
    // attributes/background flags that could conceal an opaque ancestor.
    if (!check(device && device->devType() == QInternal::Image, "native raster backing store accessible")) return;
    const QImage image = static_cast<QImage *>(device)->copy();
    if (!check(!image.isNull() && image.size() == window->size() * image.devicePixelRatio(),
               "backing-store coordinates match the undecorated client area")) return;
    check(image.save(root + "/backing.png"), "saved native backing.png");
    const qreal scale = image.devicePixelRatio();
    note(QString("INFO backing=%1x%2 dpr=%3 format=%4 windowAlpha=%5 translucentAttribute=%6")
             .arg(image.width()).arg(image.height()).arg(scale).arg(image.format())
             .arg(window->windowHandle()->format().alphaBufferSize())
             .arg(window->testAttribute(Qt::WA_TranslucentBackground)));
    QImage preview(image.size(), QImage::Format_RGB32);
    QPainter painter(&preview);
    for (int y = 0; y < image.height(); y += 20)
        for (int x = 0; x < image.width(); x += 20)
            painter.fillRect(x, y, 20, 20, ((x / 20 + y / 20) % 2) ? QColor(90, 110, 130) : QColor(210, 220, 230));
    painter.drawImage(QRect(QPoint(), image.size()), image);
    painter.end();
    check(preview.save(root + "/checkerboard-preview.png"), "saved checkerboard-preview.png (not a compositor screenshot)");
    const bool wayland = QGuiApplication::platformName() == "wayland";
    if (!wayland) note("SKIP compositor alpha: offscreen is interaction-only; use --backend=wayland");
    if (wayland) {
        check(window->windowHandle()->isExposed() && window->windowHandle()->format().alphaBufferSize() > 0
                  && window->testAttribute(Qt::WA_TranslucentBackground) && image.hasAlphaChannel(),
              "Wayland window naturally has an exposed alpha surface (no forced attributes)");
        const auto terminals = widgets(window, "Konsole::TerminalDisplay", true);
        bool translucentTerminal = !terminals.isEmpty();
        for (auto *terminal : terminals) {
            const QPoint point = terminal->mapTo(window, terminal->rect().center()) * scale;
            const int alpha = image.pixelColor(point).alpha();
            note(QString("INFO terminal center alpha=%1").arg(alpha));
            translucentTerminal &= alpha > 0 && alpha < 250;
        }
        check(translucentTerminal, "profile transparency is active in the real Wayland backing store");
    }
    for (auto *splitter : splitters) {
        for (int i = 1; i < splitter->count(); ++i) {
            auto *handle = splitter->handle(i);
            if (!handle->isVisible()) continue;
            QRect area(handle->mapTo(window, QPoint()), handle->size());
            if (splitter->orientation() == Qt::Horizontal) area.adjust(0, 12, 0, -12);
            else area.adjust(12, 0, -12, 0);
            int maxAlpha = 0;
            int count = 0;
            for (int y = area.top(); y <= area.bottom(); ++y) {
                for (int x = area.left(); x <= area.right(); ++x) {
                    const QPoint point = QPoint(x, y) * scale;
                    if (image.rect().contains(point)) {
                        maxAlpha = std::max(maxAlpha, image.pixelColor(point).alpha());
                        ++count;
                    }
                }
            }
            const QString detail = QString("%1 gutter: samples=%2 max-alpha=%3 rect=%4,%5 %6x%7")
                .arg(splitter->orientation() == Qt::Horizontal ? "left/right" : "top/bottom")
                .arg(count).arg(maxAlpha).arg(area.x()).arg(area.y()).arg(area.width()).arg(area.height());
            if (wayland) check(count > 0 && maxAlpha > 0 && maxAlpha <= 128, "translucent " + detail);
            else note("INFO " + detail);
        }
    }
}

void run()
{
    report.setFileName(root + "/results.txt");
    if (!report.open(QIODevice::WriteOnly | QIODevice::Text)) std::_Exit(2);
    QMainWindow *window = nullptr;
    const bool ready = QTest::qWaitFor([&] {
        for (auto *widget : QApplication::topLevelWidgets()) {
            if (widget->inherits("Konsole::MainWindow")) window = qobject_cast<QMainWindow *>(widget);
        }
        return window && !widgets(window, "Konsole::TerminalDisplay", true).isEmpty();
    }, 10000);
    if (!check(ready, "disposable Konsole main window ready")) {
        QCoreApplication::exit(1);
        return;
    }
    window->resize(1100, 740);
    window->activateWindow();
    QTest::qWait(500);
    note(QString("INFO Qt=%1 platform=%2 style=%3 palette.Window=%4 palette.Highlight=%5")
             .arg(qVersion(), QGuiApplication::platformName(), qApp->style()->metaObject()->className(),
                  qApp->palette().color(QPalette::Window).name(), qApp->palette().color(QPalette::Highlight).name()));
    auto *kvantum = QStyleFactory::create("kvantum");
    check(kvantum && qApp->style()->pixelMetric(QStyle::PM_SplitterWidth) == kvantum->pixelMetric(QStyle::PM_SplitterWidth)
              && qApp->palette().color(QPalette::Window) == QColor("#3d3d3e")
              && qApp->palette().color(QPalette::Highlight) == QColor("#a21215"),
          "Kvantum KvFlatRed style and application palette loaded");
    delete kvantum;
    check(window->menuBar()->isHidden(), "native --hide-menubar starts hidden");
    chrome(window, "startup");
    auto *tabs = window->findChild<QTabWidget *>();
    if (!check(tabs && tabs->inherits("Konsole::TabbedViewContainer"), "native tab container found")) {
        QCoreApplication::exit(1);
        return;
    }
    check(tabs->tabPosition() == QTabWidget::South, "tab bar stays at Bottom");
    trigger(window, "split-view-left-right");
    trigger(window, "split-view-top-bottom");
    check(widgets(window, "Konsole::TerminalDisplay", true).size() == 3, "nested splits created three visible panes");
    chrome(window, "nested splits");
    QList<QSplitter *> splitters;
    bool horizontal = false;
    bool vertical = false;
    for (auto *splitter : window->findChildren<QSplitter *>()) {
        if (!splitter->inherits("Konsole::ViewSplitter") || splitter->count() < 2) continue;
        splitters.append(splitter);
        const bool leftRight = splitter->orientation() == Qt::Horizontal;
        horizontal |= leftRight;
        vertical |= !leftRight;
        auto *handle = splitter->handle(1);
        const int extent = leftRight ? handle->width() : handle->height();
        check(splitter->handleWidth() == 5 && extent == 5 && qFuzzyCompare(handle->devicePixelRatioF(), 1.0),
              QString("%1 handleWidth=%2 physical extent=%3, expected 5px at scale 1")
                  .arg(leftRight ? "left/right" : "top/bottom").arg(splitter->handleWidth()).arg(extent));
        const auto before = splitter->sizes();
        const QPoint start = handle->rect().center();
        const QPoint end = start + (leftRight ? QPoint(60, 0) : QPoint(0, 60));
        QTest::mousePress(handle, Qt::LeftButton, Qt::NoModifier, start);
        // Send to this widget only. QTest::mouseMove can move a platform cursor;
        // explicit mouse events retain the pressed-button state without doing so.
        QMouseEvent move(QEvent::MouseMove, end, handle->mapToGlobal(end), Qt::NoButton, Qt::LeftButton, Qt::NoModifier);
        QApplication::sendEvent(handle, &move);
        QTest::mouseRelease(handle, Qt::LeftButton, Qt::NoModifier, end);
        QTest::qWait(100);
        check(splitter->sizes() != before && std::abs(splitter->sizes().first() - before.first()) >= 30,
              QString("%1 native handle drag resizes panes").arg(leftRight ? "left/right" : "top/bottom"));
    }
    check(horizontal && vertical, "both splitter orientations exercised");
    trigger(window, "equal-size-view");
    for (auto *splitter : splitters) {
        const auto sizes = splitter->sizes();
        check(*std::max_element(sizes.begin(), sizes.end()) - *std::min_element(sizes.begin(), sizes.end()) <= 1,
              "equalize restores equal pane sizes");
    }
    trigger(window, "options_configure");
    QPointer<QWidget> settings = widgets(window, "Konsole::ConfigurationDialog", true).value(0);
    if (check(settings, "Configure Konsole dialog opens normally")) {
        auto *remember = settings->findChild<QCheckBox *>("kcfg_RememberWindowSize");
        auto *buttons = settings->findChild<QDialogButtonBox *>();
        auto *apply = buttons ? buttons->button(QDialogButtonBox::Apply) : nullptr;
        QSignalSpy changed(settings, SIGNAL(settingsChanged()));
        if (check(remember && remember->isVisible() && apply && changed.isValid(), "settings checkbox, Apply and change notification available")) {
            const bool original = remember->isChecked();
            for (const bool expected : {!original, original}) {
                const QString stage = expected == original ? "settings revert" : "settings Apply";
                const auto before = changed.size();
                remember->setFocus();
                QTest::keyClick(remember, Qt::Key_Space);
                check(remember->isChecked() == expected && apply->isEnabled(), stage + ": preference modified through UI");
                QTest::mouseClick(apply, Qt::LeftButton);
                check(QTest::qWaitFor([&] { return changed.size() > before && !apply->isEnabled(); }, 2000),
                      stage + ": normal settings save completed");
                QSettings config(root + "/config/konsolerc", QSettings::IniFormat);
                // KConfig removes the entry when saving its upstream default (true).
                check(config.value("KonsoleWindow/RememberWindowSize", true).toBool() == expected,
                      stage + ": fixture preference persisted");
                chrome(window, stage);
                for (auto *splitter : splitters) {
                    auto *handle = splitter->handle(1);
                    const int extent = splitter->orientation() == Qt::Horizontal ? handle->width() : handle->height();
                    check(splitter->handleWidth() == 5 && extent == 5,
                          stage + (splitter->orientation() == Qt::Horizontal ? ": left/right" : ": top/bottom")
                              + " native handle and physical extent remain 5px");
                }
            }
        }
        if (buttons && buttons->button(QDialogButtonBox::Cancel)) QTest::mouseClick(buttons->button(QDialogButtonBox::Cancel), Qt::LeftButton);
        check(QTest::qWaitFor([&] { return !settings || !settings->isVisible(); }, 1000), "settings dialog closes after applied revert");
    }
    pixels(window, splitters);
    trigger(window, "toggle-maximize-current-view");
    check(widgets(window, "Konsole::TerminalDisplay", true).size() == 1, "maximize leaves one visible pane");
    trigger(window, "toggle-maximize-current-view");
    check(widgets(window, "Konsole::TerminalDisplay", true).size() == 3, "unmaximize restores nested panes");
    chrome(window, "unmaximize");
    trigger(window, "new-tab");
    check(tabs->count() == 2, "new tab created");
    check(tabs->tabPosition() == QTabWidget::South && tabs->tabBar()->isVisible()
              && tabs->tabBar()->geometry().top() >= tabs->currentWidget()->mapTo(tabs, QPoint()).y() + tabs->currentWidget()->height(),
          "visible tab bar is physically below the terminal area");
    chrome(window, "new tab");
    trigger(window, "previous-tab");
    check(tabs->currentIndex() == 0 && widgets(window, "Konsole::TerminalDisplay", true).size() == 3, "switch back restores split tab");
    chrome(window, "switch back");
    trigger(window, "next-tab");
    check(tabs->currentIndex() == 1, "switch forward restores new tab");
    chrome(window, "switch forward");

    auto *terminal = widgets(window, "Konsole::TerminalDisplay", true).value(0);
    if (check(terminal, "active disposable terminal found for keyboard tests")) {
        window->activateWindow();
        terminal->setFocus();
        QTest::qWait(100);
        note(QString("INFO input active=%1 focus=%2").arg(window->isActiveWindow())
                 .arg(QApplication::focusWidget() ? QApplication::focusWidget()->metaObject()->className() : "none"));
        QTest::keyClick(terminal, Qt::Key_M, Qt::ControlModifier | Qt::ShiftModifier);
        check(QTest::qWaitFor([&] { return window->menuBar()->isVisible(); }, 1000), "Ctrl+Shift+M restores native menubar");
        QTest::keyClick(terminal, Qt::Key_M, Qt::ControlModifier | Qt::ShiftModifier);
        check(QTest::qWaitFor([&] { return window->menuBar()->isHidden(); }, 1000), "Ctrl+Shift+M hides menubar again");

        auto *clipboard = QApplication::clipboard();
        clipboard->setText("printf 'GUI_%s\\n' CLIPBOARD_OK");
        QTest::qWait(100);
        QTest::keyClick(terminal, Qt::Key_V, Qt::ControlModifier | Qt::ShiftModifier);
        QTest::keyClick(terminal, Qt::Key_Return);
        QTest::qWait(400);
        clipboard->setText("sentinel-before-copy");
        trigger(window, "select-all");
        QTest::keyClick(terminal, Qt::Key_C, Qt::ControlModifier | Qt::ShiftModifier);
        check(QTest::qWaitFor([&] { return clipboard->text().contains("GUI_CLIPBOARD_OK"); }, 2000),
              "clipboard paste executes in disposable /bin/sh; copy reads actual terminal output");

        QTest::keyClick(terminal, Qt::Key_F, Qt::ControlModifier | Qt::ShiftModifier);
        auto *search = terminal->findChild<QLineEdit *>("search-edit");
        if (!search) search = window->findChild<QLineEdit *>("search-edit");
        if (check(search && search->isVisible(), "Ctrl+Shift+F opens find")) {
            search->setFocus();
            QTest::keyClick(search, Qt::Key_A, Qt::ControlModifier);
            QTest::keyClicks(search, "NO_SUCH_GUI_NEEDLE_987654");
            check(QTest::qWaitFor([&] { return !search->styleSheet().isEmpty(); }, 2000), "find completes a missing-text search");
            const QString missingStyle = search->styleSheet();
            QTest::keyClick(search, Qt::Key_A, Qt::ControlModifier);
            QTest::keyClicks(search, "GUI_CLIPBOARD_OK");
            check(QTest::qWaitFor([&] { return search->styleSheet() != missingStyle; }, 2000),
                  "find distinguishes real output from absent text");
            QTest::keyClick(search, Qt::Key_Escape);
            check(!search->isVisible(), "Escape closes find");
        }

        trigger(window, "previous-tab");
        check(tabs->currentIndex() == 0 && widgets(window, "Konsole::TerminalDisplay", true).size() == 3,
              "return from another tab to active split before mouse/menu checks");
        terminal = widgets(window, "Konsole::TerminalDisplay", true).value(0);
        if (!check(terminal, "restored split terminal exists")) {
            QCoreApplication::exit(1);
            return;
        }
        terminal->setFocus();
        QTest::qWait(100);
        clipboard->setText("popup-paste-fixture");
        if (QGuiApplication::platformName() == "offscreen") {
            QSignalSpy requests(terminal, SIGNAL(configureRequest(QPoint)));
            QSignalSpy mouse(terminal, SIGNAL(mouseSignal(int,int,int,int)));
            check(requests.isValid() && mouse.isValid(), "native context and terminal mouse signals observable");
            // A scoped timer also closes unexpected modal popups. Completion is
            // asserted outside its callback, so an unrun timer cannot pass a test.
            const auto popup = [&](const QString &kind, const auto &open, bool expected = true) {
                check(!QApplication::activePopupWidget(), kind + ": starts without a stale popup");
                int inspections = 0;
                QTimer timer;
                timer.setSingleShot(true);
                QObject::connect(&timer, &QTimer::timeout, &timer, [&] {
                    auto *menu = qobject_cast<QMenu *>(QApplication::activePopupWidget());
                    check(bool(menu && menu->isVisible()) == expected, kind + (expected ? ": popup opens" : ": no popup"));
                    if (menu) {
                        QStringList labels;
                        const auto collect = [&](const auto &self, QMenu *current) -> void {
                            for (auto *entry : current->actions()) {
                                if (!entry->isVisible() || !entry->isEnabled()) continue;
                                labels.append(entry->text().remove('&').toLower());
                                if (entry->menu()) self(self, entry->menu());
                            }
                        };
                        collect(collect, menu);
                        const QString text = labels.join('|');
                        check(text.contains("paste") && text.contains("split view") && text.contains("profile"),
                              kind + ": enabled paste, split, and profile actions survive menu merge");
                        check(menu->actions().contains(action(window, "edit_paste")), kind + ": paste belongs to the active session");
                        check(menu->grab().save(root + "/" + kind + ".png"), "saved " + kind + ".png");
                        menu->close();
                    }
                    ++inspections;
                });
                timer.start(250);
                open();
                check(QTest::qWaitFor([&] { return inspections == 1; }, 1500), kind + ": popup inspection completed exactly once");
            };
            popup("Shift-F10-menu", [&] { QTest::keyClick(terminal, Qt::Key_F10, Qt::ShiftModifier); });
            const QPoint point = terminal->mapTo(window, terminal->rect().center());
            // Qt 6.10 QWidgetWindow synthesizes context menus from right-button
            // events. The QWindow overload exercises that path entirely in-process.
            const auto rightClick = [&](Qt::KeyboardModifiers modifiers) {
                // Closing an offscreen popup can leave Qt's last mouse receiver
                // on another pane. Move back over the target before each click.
                QTest::mouseMove(window->windowHandle(), point + QPoint(2, 0));
                QTest::mouseMove(window->windowHandle(), point);
                mouse.clear();
                check(window->childAt(point) == terminal, "right-click hit test targets the active split terminal");
                QTest::mouseClick(window->windowHandle(), Qt::RightButton, modifiers, point);
            };
            requests.clear();
            mouse.clear();
            popup("split-right-click", [&] { rightClick(Qt::NoModifier); });
            check(requests.size() == 1 && mouse.isEmpty(), "plain right-click on restored split opens context without terminal mouse reports");

            // Only shell builtins: read consumes mouse input instead of allowing
            // escape bytes to become a shell command; its result stays in HOME.
            QTest::keyClicks(terminal, "printf '\\033[?1000h\\033[?1006h'; IFS= read -r mouse_report; printf '%s' \"$mouse_report\" > \"$HOME/mouse-report\"; printf '\\033[?1000l\\033[?1006l'");
            QTest::keyClick(terminal, Qt::Key_Return);
            QTest::qWait(300);
            requests.clear();
            mouse.clear();
            popup("tracking-right-click", [&] { rightClick(Qt::NoModifier); }, false);
            check(requests.isEmpty() && mouse.size() == 2
                      && mouse.at(0).at(0).toInt() == 2 && mouse.at(0).at(3).toInt() == 0
                      && mouse.at(1).at(0).toInt() == 2 && mouse.at(1).at(3).toInt() == 2,
                  "tracking plain right-click forwards right-button press/release, not a context request");
            mouse.clear();
            popup("tracking-shift-right-click", [&] { rightClick(Qt::ShiftModifier); });
            check(requests.size() == 1 && mouse.isEmpty(), "Shift+right-click bypasses tracking and opens context without mouse reports");
            QTest::keyClick(terminal, Qt::Key_Return);
            check(QTest::qWaitFor([&] { return QFileInfo::exists(root + "/home/mouse-report"); }, 2000), "disposable shell consumed mouse report and completed read");
            QFile received(root + "/home/mouse-report");
            const QRegularExpression sgr("^\\x1b\\[<2;([0-9]+);([0-9]+)M\\x1b\\[<2;\\1;\\2m$");
            check(received.open(QIODevice::ReadOnly) && sgr.match(QString::fromLatin1(received.readAll())).hasMatch(),
                  "shell received exactly one SGR right-button press/release pair");
            QTest::qWait(100);
            requests.clear();
            mouse.clear();
            popup("tracking-reset-right-click", [&] { rightClick(Qt::NoModifier); });
            check(requests.size() == 1 && mouse.isEmpty(), "shell reset restores ordinary right-click context behavior");
        } else {
            note("SKIP Wayland popup grabs: QTest has no compositor input serial; run offscreen for Shift+F10/context menus");
        }
    }

    const int beforeClose = widgets(window, "Konsole::TerminalDisplay").size();
    trigger(window, "close-session");
    check(QTest::qWaitFor([&] { return widgets(window, "Konsole::TerminalDisplay").size() == beforeClose - 1; }, 3000),
          "close-session closes exactly one disposable split pane");
    chrome(window, "close pane");
    note(QString("SUMMARY failures=%1 backend=%2").arg(failures).arg(QGuiApplication::platformName()));
    QFile complete(root + "/complete");
    if (complete.open(QIODevice::WriteOnly)) complete.write(QByteArray::number(failures));
    report.close();
    // Normal application teardown owns all the remaining disposable PTYs.
    QCoreApplication::exit(failures ? 1 : 0);
}

void startup()
{
    root = qEnvironmentVariable("KONSOLE_STYLE_GUI_ROOT");
    const auto canonical = [](const QString &path) { return QFileInfo(path).canonicalFilePath(); };
    bool safe = root.startsWith("/tmp/opencode/konsole-style-gui.") && canonical(root) == root
        && qEnvironmentVariable("KONSOLE_STYLE_GUI_PID").toLongLong() == getpid();
    const QList<QPair<const char *, const char *>> paths = {
        {"HOME", "/home"}, {"XDG_CONFIG_HOME", "/config"}, {"XDG_CONFIG_DIRS", "/config-dirs"},
        {"XDG_DATA_HOME", "/data"}, {"XDG_DATA_DIRS", "/data-dirs"}, {"XDG_STATE_HOME", "/state"},
        {"XDG_CACHE_HOME", "/cache"}, {"XDG_RUNTIME_DIR", "/runtime"}, {"TMPDIR", "/tmp"}
    };
    for (const auto &path : paths) {
        const bool valid = canonical(qEnvironmentVariable(path.first)) == root + path.second;
        if (!valid) std::fprintf(stderr, "GUARD invalid %s=%s\n", path.first, qPrintable(qEnvironmentVariable(path.first)));
        safe &= valid;
    }
    for (const auto &name : QProcessEnvironment::systemEnvironment().keys()) {
        if (name.startsWith("KONSOLE_DBUS_") || name == "DISPLAY" || name == "QT_QPA_PLATFORMTHEME"
            || name == "XDG_ACTIVATION_TOKEN" || name == "DESKTOP_STARTUP_ID" || name == "SESSION_MANAGER") {
            std::fprintf(stderr, "GUARD forbidden variable %s\n", qPrintable(name));
            safe = false;
        }
    }
    const QString backend = qEnvironmentVariable("KONSOLE_STYLE_GUI_BACKEND");
    safe &= (backend == "offscreen" && !qEnvironmentVariableIsSet("WAYLAND_DISPLAY"))
        || (backend == "wayland" && qEnvironmentVariable("WAYLAND_DISPLAY") == "konsole-style-test"
            && canonical(root + "/runtime") == root + "/runtime");
    safe &= qEnvironmentVariable("DBUS_SESSION_BUS_ADDRESS").startsWith("unix:path=" + root + "/runtime/");
    safe &= qEnvironmentVariable("DBUS_SYSTEM_BUS_ADDRESS") == "unix:path=" + root + "/runtime/no-system-bus";
    QFile guard(root + "/guard");
    safe &= guard.open(QIODevice::ReadOnly) && guard.readAll() == "isolated-konsole-style-gui-v1\n";
    if (!safe) {
        std::fprintf(stderr, "GUARD root=%s pid=%lld expected=%s backend=%s wayland=%s bus=%s\n", qPrintable(root),
                     static_cast<long long>(getpid()), qgetenv("KONSOLE_STYLE_GUI_PID").constData(), qPrintable(backend),
                     qgetenv("WAYLAND_DISPLAY").constData(), qgetenv("DBUS_SESSION_BUS_ADDRESS").constData());
        std::fputs("REFUSED unsafe Konsole GUI harness environment\n", stderr);
        std::_Exit(2);
    }
    // Do not propagate the library to the shell or any other child executable.
    qunsetenv("LD_PRELOAD");
    qunsetenv("KONSOLE_STYLE_GUI_PID");
    QTimer::singleShot(1000, QCoreApplication::instance(), run);
}
}

Q_COREAPP_STARTUP_FUNCTION(startup)
