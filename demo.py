#!/usr/bin/env python3
"""Video Processing Pipeline - Live Demo Walkthrough."""

from src.demo_utils import (
    API_BASE_URL,
    TUS_ENDPOINT,
    check_api_health,
    get_job_id_by_upload,
    parse_args,
    poll_job_progress,
    prepare_input_video,
    print_job_summary,
    upload_video_via_tus,
)


def main():
    # Шаг 1. Конфигурация и подготовка видеофайла
    args = parse_args()
    video_path = prepare_input_video(args.input_file, args.duration)

    # Шаг 2. Проверка доступности API и TUS-эндпоинта
    check_api_health(API_BASE_URL, TUS_ENDPOINT)

    # Шаг 3. Докачиваемая TUS-загрузка видео в S3
    upload_id = upload_video_via_tus(TUS_ENDPOINT, video_path, args)

    # Шаг 4. Получение задачи BullMQ из сессии TUS
    job_id = get_job_id_by_upload(TUS_ENDPOINT, upload_id)
    if not job_id:
        return

    # Шаг 5. Мониторинг выполнения задачи и стриминг прогресса
    job_result = poll_job_progress(API_BASE_URL, job_id)

    # Шаг 6. Итоговый отчет: тайминги, профили HLS, метрики качества и плеер
    print_job_summary(job_result, API_BASE_URL, compute_quality=bool(args.vmaf or args.xpsnr))


if __name__ == "__main__":
    main()
