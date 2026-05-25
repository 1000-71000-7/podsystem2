(function() {
    // Конфигурация
    const config = {
        apiUrl: window.location.origin + '/api/collect',
        trackerId: '{{TRACKER_ID}}',  // Заменится динамически
        sessionId: null,
        startTime: Date.now(),
        formStartTimes: new Map(),  // Время начала заполнения каждой формы
        formFields: new Map()        // Отслеживание заполненных полей
    };

    // Генерация ID сессии
    function getSessionId() {
        if (config.sessionId) return config.sessionId;

        let sessionId = sessionStorage.getItem('monitor_session_id');
        if (!sessionId) {
            sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('monitor_session_id', sessionId);
        }
        config.sessionId = sessionId;
        return sessionId;
    }

    // Отправка события
    function sendEvent(eventType, eventData = {}) {
        const data = {
            tracker_id: config.trackerId,
            session_id: getSessionId(),
            event_type: eventType,
            url: window.location.href,
            referrer: document.referrer,
            event_data: eventData,
            timestamp: Date.now(),
            time_on_page: Math.floor((Date.now() - config.startTime) / 1000)
        };

        // Используем sendBeacon для надёжности при уходе
        if (eventType === 'page_exit' && navigator.sendBeacon) {
            navigator.sendBeacon(config.apiUrl, JSON.stringify(data));
        } else {
            fetch(config.apiUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data),
                keepalive: true
            }).catch(console.error);
        }
    }

    // === 1. КОНТРОЛЬ КАЧЕСТВА ЗАГРУЗКИ (Core Web Vitals) ===
    function collectCoreWebVitals() {
        // LCP (Largest Contentful Paint)
        try {
            let lcpValue = 0;
            const lcpObserver = new PerformanceObserver((list) => {
                const entries = list.getEntries();
                const lastEntry = entries[entries.length - 1];
                lcpValue = lastEntry.startTime;
            });
            lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true });

            // Отправляем LCP при уходе со страницы
            window.addEventListener('beforeunload', () => {
                if (lcpValue > 0) {
                    sendEvent('metric_lcp', { value: Math.round(lcpValue), unit: 'ms' });
                }
            });
        } catch(e) { console.warn('LCP not supported', e); }

        // INP (Interaction to Next Paint) — новая метрика Core Web Vitals
        try {
            let inpValue = 0;
            const inpObserver = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    const duration = entry.processingEnd - entry.startTime;
                    if (duration > inpValue) inpValue = duration;
                }
            });
            inpObserver.observe({ type: 'event', buffered: true });

            window.addEventListener('beforeunload', () => {
                if (inpValue > 0) {
                    sendEvent('metric_inp', { value: Math.round(inpValue), unit: 'ms' });
                }
            });
        } catch(e) { console.warn('INP not supported', e); }

        // CLS (Cumulative Layout Shift)
        try {
            let clsValue = 0;
            const clsObserver = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (!entry.hadRecentInput) {
                        clsValue += entry.value;
                    }
                }
            });
            clsObserver.observe({ type: 'layout-shift', buffered: true });

            window.addEventListener('beforeunload', () => {
                if (clsValue > 0) {
                    sendEvent('metric_cls', { value: clsValue.toFixed(3), unit: 'score' });
                }
            });
        } catch(e) { console.warn('CLS not supported', e); }
    }

    // === 2. ОТСЛЕЖИВАНИЕ ФОРМ ОБРАЩЕНИЙ ===
    function setupFormTracking() {
        // Находим все формы на странице
        const forms = document.querySelectorAll('form');

        forms.forEach((form, index) => {
            const formId = form.id || `form_${index}`;
            const formName = form.name || form.action || 'unknown_form';

            // Отслеживаем начало взаимодействия с формой
            let formInteractionStarted = false;

            const startFormInteraction = () => {
                if (!formInteractionStarted) {
                    formInteractionStarted = true;
                    config.formStartTimes.set(formId, Date.now());
                    sendEvent('form_start', {
                        form_id: formId,
                        form_name: formName,
                        form_action: form.action
                    });
                }
            };

            // События: фокус на любом поле формы
            form.addEventListener('focusin', startFormInteraction);

            // Отслеживаем заполнение каждого поля
            const fields = form.querySelectorAll('input, textarea, select');
            fields.forEach(field => {
                const fieldName = field.name || field.id || field.type;
                let fieldFilled = false;

                field.addEventListener('blur', () => {
                    if (field.value && field.value.trim() !== '' && !fieldFilled) {
                        fieldFilled = true;

                        // Обновляем счётчик заполненных полей для формы
                        const filled = config.formFields.get(formId) || [];
                        if (!filled.includes(fieldName)) {
                            filled.push(fieldName);
                            config.formFields.set(formId, filled);
                        }

                        sendEvent('form_field_filled', {
                            form_id: formId,
                            field_name: fieldName,
                            field_type: field.type,
                            fields_count: filled.length,
                            total_fields: fields.length
                        });
                    }
                });
            });

            // Отслеживаем попытку отправки
            form.addEventListener('submit', (e) => {
                const startTime = config.formStartTimes.get(formId);
                const timeSpent = startTime ? Math.floor((Date.now() - startTime) / 1000) : null;
                const filledFields = config.formFields.get(formId) || [];

                sendEvent('form_submit_attempt', {
                    form_id: formId,
                    form_name: formName,
                    time_spent_seconds: timeSpent,
                    fields_filled: filledFields.length,
                    total_fields: fields.length,
                    completion_rate: Math.round((filledFields.length / fields.length) * 100)
                });
            });

            // Отслеживаем ошибки валидации HTML5
            form.addEventListener('invalid', (e) => {
                if (e.target) {
                    sendEvent('form_validation_error', {
                        form_id: formId,
                        field_name: e.target.name || e.target.id,
                        error_message: e.target.validationMessage || 'Invalid field'
                    });
                }
            }, true);
        });
    }

    // === 3. ОТСЛЕЖИВАНИЕ ПРОСМОТРОВ И УХОДА ===
    function trackPageExit() {
        let isExiting = false;

        window.addEventListener('beforeunload', () => {
            if (isExiting) return;
            isExiting = true;

            const timeOnPage = Math.floor((Date.now() - config.startTime) / 1000);
            sendEvent('page_exit', {
                time_on_page_seconds: timeOnPage,
                scroll_depth: getScrollDepth(),
                has_interacted: config.hasInteracted || false
            });
        });
    }

    // Глубина прокрутки
    function getScrollDepth() {
        const winHeight = window.innerHeight;
        const docHeight = document.documentElement.scrollHeight;
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        const maxScroll = docHeight - winHeight;

        if (maxScroll <= 0) return 100;
        return Math.round((scrollTop / maxScroll) * 100);
    }

    // Отмечаем взаимодействие с сайтом
    document.addEventListener('click', () => { config.hasInteracted = true; });
    document.addEventListener('keydown', () => { config.hasInteracted = true; });
    document.addEventListener('scroll', () => { config.hasInteracted = true; });

    // === 4. ЗАПИСЬ ПУТИ ПОЛЬЗОВАТЕЛЯ (User Journey) ===
    let pageViewId = Math.random().toString(36).substr(2, 9);
    let previousPages = [];

    function recordUserJourney() {
        // Сохраняем текущую страницу в историю
        const currentPage = {
            url: window.location.href,
            title: document.title,
            referrer: document.referrer,
            timestamp: Date.now()
        };

        // Загружаем историю из sessionStorage
        const stored = sessionStorage.getItem('user_journey');
        let journey = stored ? JSON.parse(stored) : [];

        // Добавляем текущую страницу
        journey.push(currentPage);

        // Ограничиваем историю 20 страницами
        if (journey.length > 20) journey.shift();

        sessionStorage.setItem('user_journey', JSON.stringify(journey));

        // Отправляем событие перехода
        sendEvent('page_view', {
            page_view_id: pageViewId,
            page_title: document.title,
            journey_step: journey.length,
            previous_url: previousPages[previousPages.length - 1] || null
        });

        previousPages.push(window.location.href);
    }

    // === 5. МОНИТОРИНГ ОШИБОК JS ===
    window.addEventListener('error', (e) => {
        sendEvent('js_error', {
            message: e.message,
            filename: e.filename,
            line: e.lineno,
            col: e.colno
        });
    });

    // === ИНИЦИАЛИЗАЦИЯ ===
    collectCoreWebVitals();
    setupFormTracking();
    trackPageExit();
    recordUserJourney();

    console.log('🚀 Монитор обращений граждан v2.0 запущен');
})();