(function () {
  'use strict';
  const t = (text) => (window.DP_I18N && window.DP_I18N[text]) || text;

  const palette = [
    '#0071e3',
    '#1f8a70',
    '#e8870e',
    '#6e6e73',
    '#0b3d6e',
    '#c9a227',
    '#5aa9e6',
    '#a5662d'
  ];

  const instances = new Map();
  const jobs = new Map();
  const observers = new Map();
  const downloadHandlers = new Map();
  const measureHandlers = new Map();

  function prefersReducedMotion() {
    return Boolean(
      window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    );
  }

  function rootContains(root, element) {
    if (!root || !element) return false;
    if (root === document) return document.documentElement.contains(element);
    return root === element || Boolean(root.contains && root.contains(element));
  }

  function findInRoot(root, selector) {
    if (!root) return null;
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(selector)) return root;
    return root.querySelector ? root.querySelector(selector) : null;
  }

  function findAllInRoot(root, selector) {
    if (!root) return [];
    const matches = root.querySelectorAll ? Array.from(root.querySelectorAll(selector)) : [];
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(selector)) matches.unshift(root);
    return matches;
  }

  function chartErrorElement(canvas) {
    return canvas
      ?.closest('.chart-card')
      ?.querySelector('.chart-render-error') || null;
  }

  function hideChartError(canvas) {
    const error = chartErrorElement(canvas);
    if (!error) return;
    error.hidden = true;
    error.removeAttribute('role');
    error.removeAttribute('aria-live');
  }

  function showChartError(canvas, message) {
    if (!canvas) return;
    canvas.hidden = true;
    canvas.removeAttribute('aria-busy');
    const error = chartErrorElement(canvas);
    if (!error) return;
    if (message) error.textContent = message;
    error.hidden = false;
    error.setAttribute('role', 'status');
    error.setAttribute('aria-live', 'polite');
  }

  function destroyCanvas(canvas) {
    const knownInstance = instances.get(canvas);
    const chartInstance = knownInstance ||
      (window.Chart && typeof window.Chart.getChart === 'function'
        ? window.Chart.getChart(canvas)
        : null);

    if (chartInstance && typeof chartInstance.destroy === 'function') {
      try {
        chartInstance.destroy();
      } catch (_error) {
        // A detached canvas can already have been released by Chart.js.
      }
    }

    instances.delete(canvas);
    jobs.delete(canvas);
    delete canvas.dataset.rendered;
    canvas.removeAttribute('aria-busy');
  }

  function normalizedChart(chart) {
    if (!chart || typeof chart !== 'object') return null;
    const labels = Array.isArray(chart.labels) ? chart.labels : [];
    const values = Array.isArray(chart.values) ? chart.values : [];
    if (!labels.length || !values.length || labels.length !== values.length) return null;

    const allowedTypes = new Set(['bar', 'line', 'doughnut']);
    return {
      ...chart,
      type: allowedTypes.has(chart.type) ? chart.type : 'bar',
      labels,
      values
    };
  }

  function renderDataPilotChart(canvas, rawChart) {
    if (!canvas) return null;

    destroyCanvas(canvas);
    hideChartError(canvas);
    canvas.hidden = false;
    canvas.setAttribute('aria-busy', 'true');

    const chart = normalizedChart(rawChart);
    if (!chart) {
      showChartError(canvas, t('Ce graphique ne contient pas assez de valeurs fiables pour être affiché.'));
      return null;
    }

    if (typeof window.Chart === 'undefined') {
      showChartError(canvas, t('Le module de graphiques est indisponible pour le moment.'));
      return null;
    }

    const circular = chart.type === 'doughnut';
    const line = chart.type === 'line';
    const background = circular
      ? chart.values.map((_value, index) => palette[index % palette.length])
      : line
        ? 'rgba(0, 113, 227, .08)'
        : '#0071e3';

    try {
      const instance = new window.Chart(canvas, {
        type: chart.type,
        data: {
          labels: chart.labels,
          datasets: [{
            label: chart.dataset_label || t('Valeur'),
            data: chart.values,
            backgroundColor: background,
            borderColor: line ? '#0071e3' : circular ? '#ffffff' : '#0071e3',
            borderWidth: circular ? 3 : line ? 2.5 : 0,
            borderRadius: circular || line ? 0 : 8,
            maxBarThickness: 44,
            pointBackgroundColor: '#0071e3',
            pointBorderColor: '#ffffff',
            pointBorderWidth: 2,
            pointRadius: line ? 3 : 0,
            pointHoverRadius: line ? 5 : 0,
            tension: line ? 0.34 : 0,
            fill: line
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          locale: window.DP_LANG === 'en' ? 'en-US' : 'fr-FR',
          indexAxis: chart.index_axis || 'x',
          animation: prefersReducedMotion()
            ? false
            : { duration: 700, easing: 'easeOutQuart' },
          transitions: {
            active: {
              animation: { duration: prefersReducedMotion() ? 0 : 140 }
            }
          },
          plugins: {
            legend: {
              display: circular,
              position: 'bottom',
              labels: {
                usePointStyle: true,
                boxWidth: 8,
                padding: 16,
                color: '#424245',
                font: { family: 'Inter', size: 11 }
              }
            },
            tooltip: {
              backgroundColor: 'rgba(29, 29, 31, .92)',
              padding: 12,
              cornerRadius: 8,
              titleFont: { family: 'Inter' },
              bodyFont: { family: 'Inter' }
            }
          },
          scales: circular
            ? {}
            : {
                y: {
                  beginAtZero: true,
                  border: { display: false },
                  grid: { color: 'rgba(0, 0, 0, .06)' },
                  ticks: {
                    color: '#6e6e73',
                    font: { family: 'Inter', size: 11 }
                  }
                },
                x: {
                  border: { display: false },
                  grid: { display: false },
                  ticks: {
                    color: '#6e6e73',
                    maxRotation: 35,
                    minRotation: 0,
                    font: { family: 'Inter', size: 11 }
                  }
                }
              }
        }
      });

      instances.set(canvas, instance);
      canvas.dataset.rendered = 'true';
      canvas.removeAttribute('aria-busy');
      return instance;
    } catch (_error) {
      showChartError(canvas, t('Le graphique ne peut pas être affiché pour le moment.'));
      return null;
    }
  }

  function readChartData(root, selector) {
    const script = findInRoot(root, selector);
    if (!script) return { charts: [], error: false };

    try {
      const parsed = JSON.parse(script.textContent || '[]');
      return { charts: Array.isArray(parsed) ? parsed : [], error: !Array.isArray(parsed) };
    } catch (_error) {
      return { charts: [], error: true };
    }
  }

  function collectJobs(root) {
    const dashboard = readChartData(root, '[data-dashboard-chart-data]');
    const custom = readChartData(root, '[data-custom-chart-data]');
    const result = [];

    dashboard.charts.forEach((chart, index) => {
      const canvas = findInRoot(root, `#chart${index}`);
      if (canvas) result.push([canvas, chart]);
    });
    custom.charts.forEach((chart, index) => {
      const canvas = findInRoot(root, `#customChart${index}`);
      if (canvas) result.push([canvas, chart]);
    });

    if (dashboard.error) {
      findAllInRoot(root, 'canvas[id^="chart"]').forEach((canvas) => {
        showChartError(canvas, t('Les données de ce graphique sont temporairement illisibles.'));
      });
    }
    if (custom.error) {
      findAllInRoot(root, 'canvas[id^="customChart"]').forEach((canvas) => {
        showChartError(canvas, t('Les données de ce graphique sont temporairement illisibles.'));
      });
    }

    return result;
  }

  function initializeCharts(root) {
    const chartJobs = collectJobs(root);
    chartJobs.forEach(([canvas, chart]) => jobs.set(canvas, chart));

    if (
      'IntersectionObserver' in window &&
      !prefersReducedMotion() &&
      chartJobs.length
    ) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const chart = jobs.get(entry.target);
          if (chart) renderDataPilotChart(entry.target, chart);
          observer.unobserve(entry.target);
        });
      }, { rootMargin: '180px 0px', threshold: 0.05 });

      observers.set(root, observer);
      chartJobs.forEach(([canvas]) => observer.observe(canvas));
      return;
    }

    chartJobs.forEach(([canvas, chart]) => renderDataPilotChart(canvas, chart));
  }

  function initializeMeasureField(root) {
    const aggregation = findInRoot(root, '#chart-aggregation');
    const measure = findInRoot(root, '#chart-measure');
    if (!aggregation || !measure) return;

    const sync = () => {
      const needsMeasure = aggregation.value !== 'count';
      measure.disabled = !needsMeasure;
      measure.required = needsMeasure;
      measure.setAttribute('aria-disabled', String(!needsMeasure));
      if (!needsMeasure) measure.value = '';
    };

    aggregation.addEventListener('change', sync);
    measureHandlers.set(root, { aggregation, sync });
    sync();
  }

  function initializeDownloads(root) {
    if (!root || !root.addEventListener) return;

    const handler = (event) => {
      const button = event.target.closest
        ? event.target.closest('.chart-download')
        : null;
      if (!button || !rootContains(root, button)) return;

      const canvasId = button.dataset.canvas;
      const canvas = canvasId ? findInRoot(root, `#${CSS.escape(canvasId)}`) : null;
      if (!canvas) return;

      if (!instances.has(canvas) && jobs.has(canvas)) {
        renderDataPilotChart(canvas, jobs.get(canvas));
      }
      if (!instances.has(canvas)) {
        showChartError(canvas, t('Le graphique doit être affiché avant de pouvoir être téléchargé.'));
        return;
      }

      const saveImage = (blob) => {
        if (!blob) {
          showChartError(canvas, t('Le téléchargement de ce graphique est indisponible pour le moment.'));
          return;
        }
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.download = 'graphique-datapilot.png';
        link.href = url;
        link.style.display = 'none';
        document.body.append(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      };

      try {
        canvas.toBlob(saveImage, 'image/png', 1);
      } catch (_error) {
        showChartError(canvas, t('Le téléchargement de ce graphique est indisponible pour le moment.'));
      }
    };

    root.addEventListener('click', handler);
    downloadHandlers.set(root, handler);
  }

  function destroy(root = document) {
    const observer = observers.get(root);
    if (observer) {
      observer.disconnect();
      observers.delete(root);
    }

    const downloadHandler = downloadHandlers.get(root);
    if (downloadHandler && root.removeEventListener) {
      root.removeEventListener('click', downloadHandler);
      downloadHandlers.delete(root);
    }

    const measureBinding = measureHandlers.get(root);
    if (measureBinding) {
      measureBinding.aggregation.removeEventListener('change', measureBinding.sync);
      measureHandlers.delete(root);
    }

    Array.from(instances.keys()).forEach((canvas) => {
      if (rootContains(root, canvas)) destroyCanvas(canvas);
    });
    Array.from(jobs.keys()).forEach((canvas) => {
      if (rootContains(root, canvas)) jobs.delete(canvas);
    });
  }

  function initialize(root = document) {
    if (!root || !root.querySelector) return;
    destroy(root);
    initializeCharts(root);
    initializeMeasureField(root);
    initializeDownloads(root);
  }

  window.renderDataPilotChart = renderDataPilotChart;
  window.DataPilotCharts = { initialize, destroy };
})();
