"""Baseline schema: every table, constraint, index, trigger and function of the platform.

This single migration replaces the earlier 0001..0013 files (merged 2026-10-24, before any deployment). It was
generated from the 13-file chain on an empty database and is checked against that chain (same pg_dump output).
Databases created from the old chain are re-stamped to this revision; their data is not touched.

Downgrade is not supported for the baseline: restore a backup instead.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Schema as pg_dump wrote it after the full chain (plain SQL, no owners or privileges).
SCHEMA_SQL = r'''
--
-- PostgreSQL database dump
--


-- Dumped from database version 14.22 (Homebrew)
-- Dumped by pg_dump version 14.22 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: enforce_draft_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_draft_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE v_status text;
BEGIN
  IF current_setting('mda.purge', true) = 'on' THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  SELECT status INTO v_status FROM schedule_versions
   WHERE id = COALESCE(NEW.version_id, OLD.version_id);
  IF TG_OP = 'UPDATE' AND NEW.version_id <> OLD.version_id THEN
    RAISE EXCEPTION 'schedule rows cannot move between versions';
  END IF;
  IF v_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'schedule version % is % and immutable', COALESCE(NEW.version_id, OLD.version_id), v_status
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: activity_days; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.activity_days (
    id uuid NOT NULL,
    station_id uuid NOT NULL,
    local_date date NOT NULL,
    kind character varying(16) NOT NULL,
    registration_opens_at timestamp with time zone NOT NULL,
    registration_closes_at timestamp with time zone NOT NULL,
    publish_target_at timestamp with time zone NOT NULL,
    latest_published_version_id uuid,
    created_at timestamp with time zone NOT NULL,
    row_version integer NOT NULL,
    CONSTRAINT ck_activity_days_kind CHECK (((kind)::text = ANY ((ARRAY['regular'::character varying, 'friday'::character varying, 'saturday'::character varying, 'holiday'::character varying, 'holiday_eve'::character varying])::text[]))),
    CONSTRAINT ck_activity_days_window_order CHECK ((registration_opens_at < registration_closes_at))
);


--
-- Name: ambulance_slots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ambulance_slots (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    slot_key uuid NOT NULL,
    ordinal smallint NOT NULL,
    capacity smallint NOT NULL,
    status character varying(16) NOT NULL,
    operator_user_id uuid,
    operator_start time without time zone,
    operator_end time without time zone,
    CONSTRAINT ck_ambulance_slots_capacity CHECK (((capacity >= 0) AND (capacity <= 8))),
    CONSTRAINT ck_ambulance_slots_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: assignment_cancellations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignment_cancellations (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    user_id uuid NOT NULL,
    cancelled_at timestamp with time zone NOT NULL
);


--
-- Name: assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignments (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    slot_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    created_by_id uuid
);


--
-- Name: audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_events (
    id uuid NOT NULL,
    actor_user_id uuid,
    action character varying(64) NOT NULL,
    object_type character varying(48) NOT NULL,
    object_id character varying(64),
    visibility character varying(16) NOT NULL,
    meta jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_audit_events_visibility CHECK (((visibility)::text = ANY ((ARRAY['normal'::character varying, 'admin_only'::character varying])::text[])))
);


--
-- Name: authority_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.authority_roles (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    role character varying(32) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    granted_by_id uuid,
    revoked_by_id uuid,
    CONSTRAINT ck_authority_roles_role CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'youth_manager'::character varying])::text[])))
);


--
-- Name: availability_attention; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.availability_attention (
    id uuid NOT NULL,
    shift_id uuid NOT NULL,
    user_id uuid NOT NULL,
    published_version_id uuid NOT NULL,
    registration_revision integer NOT NULL,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_id uuid,
    resolution character varying(32),
    CONSTRAINT ck_availability_attention_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'resolved'::character varying])::text[])))
);


--
-- Name: boarding_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.boarding_entries (
    id uuid NOT NULL,
    version_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    user_id uuid NOT NULL,
    "position" smallint NOT NULL
);


--
-- Name: day_memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.day_memberships (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    weekday smallint NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    changed_by_id uuid,
    CONSTRAINT ck_day_memberships_weekday CHECK (((weekday >= 0) AND (weekday <= 6)))
);


--
-- Name: invitations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invitations (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    activity_day_id uuid NOT NULL,
    shift_id uuid,
    invited_by_id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    responded_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_invitations_kind CHECK (((kind)::text = ANY ((ARRAY['invitation'::character varying, 'direct_add'::character varying])::text[]))),
    CONSTRAINT ck_invitations_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'confirmed'::character varying, 'expired'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: manager_added_participants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manager_added_participants (
    id uuid NOT NULL,
    shift_id uuid NOT NULL,
    user_id uuid NOT NULL,
    added_by_id uuid NOT NULL,
    added_at timestamp with time zone NOT NULL,
    removed_at timestamp with time zone,
    removed_by_id uuid
);


--
-- Name: manager_day_scopes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manager_day_scopes (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    weekday smallint NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    granted_by_id uuid,
    revoked_by_id uuid,
    CONSTRAINT ck_manager_day_scopes_weekday CHECK (((weekday >= 0) AND (weekday <= 6)))
);


--
-- Name: mentor_access_extensions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mentor_access_extensions (
    id uuid NOT NULL,
    mentor_id uuid NOT NULL,
    trainee_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    granted_by_id uuid NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revoked_by_id uuid
);


--
-- Name: mentoring_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mentoring_summaries (
    id uuid NOT NULL,
    trainee_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    strengths text,
    strengths_none boolean NOT NULL,
    improvements text,
    improvements_none boolean NOT NULL,
    version integer NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    updated_by_id uuid NOT NULL,
    CONSTRAINT ck_mentoring_summaries_improvements_exclusive CHECK ((NOT (improvements_none AND (improvements IS NOT NULL)))),
    CONSTRAINT ck_mentoring_summaries_strengths_exclusive CHECK ((NOT (strengths_none AND (strengths IS NOT NULL))))
);


--
-- Name: notification_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_outbox (
    id uuid NOT NULL,
    event_type character varying(48) NOT NULL,
    recipient_user_id uuid NOT NULL,
    dedup_key character varying(255) NOT NULL,
    payload jsonb NOT NULL,
    status character varying(16) NOT NULL,
    attempts integer NOT NULL,
    next_attempt_at timestamp with time zone NOT NULL,
    last_error text,
    channel character varying(32),
    created_at timestamp with time zone NOT NULL,
    processed_at timestamp with time zone,
    read_at timestamp with time zone,
    CONSTRAINT ck_notification_outbox_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'accepted'::character varying, 'failed'::character varying, 'simulated'::character varying])::text[])))
);


--
-- Name: personal_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_notes (
    id uuid NOT NULL,
    author_id uuid NOT NULL,
    shift_id uuid,
    content text NOT NULL,
    shared boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: progress_corrections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.progress_corrections (
    id uuid NOT NULL,
    trainee_id uuid NOT NULL,
    count_delta integer NOT NULL,
    last_ride_date date,
    reason character varying(300) NOT NULL,
    created_by_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_progress_corrections_has_effect CHECK (((count_delta <> 0) OR (last_ride_date IS NOT NULL)))
);


--
-- Name: push_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.push_subscriptions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    endpoint character varying(1024) NOT NULL,
    p256dh character varying(200) NOT NULL,
    auth character varying(64) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL
);


--
-- Name: queue_order; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.queue_order (
    shift_id uuid NOT NULL,
    user_id uuid NOT NULL,
    rank double precision NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    updated_by_id uuid
);


--
-- Name: registration_choices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registration_choices (
    id uuid NOT NULL,
    registration_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    available boolean NOT NULL,
    arrival_time time without time zone,
    is_late boolean NOT NULL,
    first_answered_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    cancelled_at timestamp with time zone,
    cancelled_by_id uuid
);


--
-- Name: registration_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registration_requests (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    activity_day_id uuid NOT NULL,
    message character varying(1000) NOT NULL,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_id uuid,
    shift_id uuid,
    CONSTRAINT ck_registration_requests_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying, 'resolved'::character varying])::text[])))
);


--
-- Name: registration_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registration_templates (
    id uuid NOT NULL,
    weekday smallint NOT NULL,
    open_days_before smallint NOT NULL,
    open_time time without time zone NOT NULL,
    close_days_before smallint NOT NULL,
    close_time time without time zone NOT NULL,
    target_days_before smallint NOT NULL,
    target_time time without time zone NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    created_by_id uuid,
    CONSTRAINT ck_registration_templates_offsets_order CHECK (((open_days_before > close_days_before) AND (close_days_before >= target_days_before))),
    CONSTRAINT ck_registration_templates_weekday CHECK (((weekday >= 0) AND (weekday <= 6)))
);


--
-- Name: registrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registrations (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    activity_day_id uuid NOT NULL,
    source character varying(16) NOT NULL,
    revision integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    updated_by_id uuid,
    CONSTRAINT ck_registrations_source CHECK (((source)::text = ANY ((ARRAY['self'::character varying, 'manager'::character varying])::text[])))
);


--
-- Name: ride_assessments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ride_assessments (
    ride_id uuid NOT NULL,
    markers jsonb NOT NULL,
    summary text,
    version integer NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    updated_by_id uuid NOT NULL
);


--
-- Name: ride_contributors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ride_contributors (
    ride_id uuid NOT NULL,
    user_id uuid NOT NULL,
    first_at timestamp with time zone NOT NULL,
    last_at timestamp with time zone NOT NULL
);


--
-- Name: rides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rides (
    id uuid NOT NULL,
    trainee_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    occurred_on date NOT NULL,
    seq integer NOT NULL,
    description character varying(200),
    description_version integer NOT NULL,
    description_updated_at timestamp with time zone,
    created_by_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    cancelled_at timestamp with time zone,
    cancelled_by_id uuid,
    cancel_reason character varying(300),
    duplicate_of_id uuid,
    CONSTRAINT ck_rides_cancel_complete CHECK ((((cancelled_at IS NULL) = (cancelled_by_id IS NULL)) AND ((cancelled_at IS NULL) = (cancel_reason IS NULL)))),
    CONSTRAINT ck_rides_duplicate_of_cancelled CHECK (((duplicate_of_id IS NULL) OR ((cancelled_at IS NOT NULL) AND (duplicate_of_id <> id))))
);


--
-- Name: schedule_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_versions (
    id uuid NOT NULL,
    activity_day_id uuid NOT NULL,
    revision integer NOT NULL,
    status character varying(16) NOT NULL,
    base_published_version_id uuid,
    created_at timestamp with time zone NOT NULL,
    created_by_id uuid,
    edit_count integer NOT NULL,
    last_edit_at timestamp with time zone,
    last_edit_by_id uuid,
    published_at timestamp with time zone,
    published_by_id uuid,
    purged_at timestamp with time zone,
    CONSTRAINT ck_schedule_versions_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'published'::character varying, 'superseded'::character varying])::text[])))
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id uuid NOT NULL,
    token_hash character varying(64) NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    last_activity_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);


--
-- Name: shifts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shifts (
    id uuid NOT NULL,
    activity_day_id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    arrival_at timestamp with time zone NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    self_selectable boolean NOT NULL,
    allows_late_arrival boolean NOT NULL,
    baseline_slots smallint NOT NULL,
    CONSTRAINT ck_shifts_kind CHECK (((kind)::text = ANY ((ARRAY['morning'::character varying, 'reinforcement'::character varying, 'evening'::character varying])::text[])))
);


--
-- Name: stations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stations (
    id uuid NOT NULL,
    name character varying(120) NOT NULL
);


--
-- Name: temporary_day_manager_appointments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.temporary_day_manager_appointments (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    weekday smallint NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    created_by_id uuid NOT NULL,
    updated_at timestamp with time zone,
    updated_by_id uuid,
    revoked_at timestamp with time zone,
    revoked_by_id uuid,
    CONSTRAINT ck_temporary_day_manager_appointments_interval CHECK ((ends_at > starts_at)),
    CONSTRAINT ck_temporary_day_manager_appointments_weekday CHECK (((weekday >= 0) AND (weekday <= 6)))
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    email character varying(320) NOT NULL,
    full_name character varying(120) NOT NULL,
    phone character varying(32),
    password_hash character varying(255) NOT NULL,
    status character varying(16) NOT NULL,
    requested_weekday smallint,
    approved_by_id uuid,
    approved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_users_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'active'::character varying, 'inactive'::character varying])::text[])))
);


--
-- Name: volunteer_level_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.volunteer_level_history (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    level character varying(16) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    changed_by_id uuid,
    CONSTRAINT ck_volunteer_level_history_level CHECK (((level)::text = ANY ((ARRAY['trainee'::character varying, 'youth'::character varying, 'mentor'::character varying])::text[])))
);


--
-- Name: activity_days pk_activity_days; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_days
    ADD CONSTRAINT pk_activity_days PRIMARY KEY (id);


--
-- Name: ambulance_slots pk_ambulance_slots; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ambulance_slots
    ADD CONSTRAINT pk_ambulance_slots PRIMARY KEY (id);


--
-- Name: assignment_cancellations pk_assignment_cancellations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_cancellations
    ADD CONSTRAINT pk_assignment_cancellations PRIMARY KEY (id);


--
-- Name: assignments pk_assignments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT pk_assignments PRIMARY KEY (id);


--
-- Name: audit_events pk_audit_events; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT pk_audit_events PRIMARY KEY (id);


--
-- Name: authority_roles pk_authority_roles; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authority_roles
    ADD CONSTRAINT pk_authority_roles PRIMARY KEY (id);


--
-- Name: availability_attention pk_availability_attention; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_attention
    ADD CONSTRAINT pk_availability_attention PRIMARY KEY (id);


--
-- Name: boarding_entries pk_boarding_entries; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boarding_entries
    ADD CONSTRAINT pk_boarding_entries PRIMARY KEY (id);


--
-- Name: day_memberships pk_day_memberships; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.day_memberships
    ADD CONSTRAINT pk_day_memberships PRIMARY KEY (id);


--
-- Name: invitations pk_invitations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT pk_invitations PRIMARY KEY (id);


--
-- Name: manager_added_participants pk_manager_added_participants; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_added_participants
    ADD CONSTRAINT pk_manager_added_participants PRIMARY KEY (id);


--
-- Name: manager_day_scopes pk_manager_day_scopes; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_day_scopes
    ADD CONSTRAINT pk_manager_day_scopes PRIMARY KEY (id);


--
-- Name: mentor_access_extensions pk_mentor_access_extensions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentor_access_extensions
    ADD CONSTRAINT pk_mentor_access_extensions PRIMARY KEY (id);


--
-- Name: mentoring_summaries pk_mentoring_summaries; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentoring_summaries
    ADD CONSTRAINT pk_mentoring_summaries PRIMARY KEY (id);


--
-- Name: notification_outbox pk_notification_outbox; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT pk_notification_outbox PRIMARY KEY (id);


--
-- Name: personal_notes pk_personal_notes; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_notes
    ADD CONSTRAINT pk_personal_notes PRIMARY KEY (id);


--
-- Name: progress_corrections pk_progress_corrections; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.progress_corrections
    ADD CONSTRAINT pk_progress_corrections PRIMARY KEY (id);


--
-- Name: push_subscriptions pk_push_subscriptions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT pk_push_subscriptions PRIMARY KEY (id);


--
-- Name: queue_order pk_queue_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_order
    ADD CONSTRAINT pk_queue_order PRIMARY KEY (shift_id, user_id);


--
-- Name: registration_choices pk_registration_choices; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_choices
    ADD CONSTRAINT pk_registration_choices PRIMARY KEY (id);


--
-- Name: registration_requests pk_registration_requests; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_requests
    ADD CONSTRAINT pk_registration_requests PRIMARY KEY (id);


--
-- Name: registration_templates pk_registration_templates; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_templates
    ADD CONSTRAINT pk_registration_templates PRIMARY KEY (id);


--
-- Name: registrations pk_registrations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registrations
    ADD CONSTRAINT pk_registrations PRIMARY KEY (id);


--
-- Name: ride_assessments pk_ride_assessments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ride_assessments
    ADD CONSTRAINT pk_ride_assessments PRIMARY KEY (ride_id);


--
-- Name: ride_contributors pk_ride_contributors; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ride_contributors
    ADD CONSTRAINT pk_ride_contributors PRIMARY KEY (ride_id, user_id);


--
-- Name: rides pk_rides; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT pk_rides PRIMARY KEY (id);


--
-- Name: schedule_versions pk_schedule_versions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT pk_schedule_versions PRIMARY KEY (id);


--
-- Name: sessions pk_sessions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT pk_sessions PRIMARY KEY (id);


--
-- Name: shifts pk_shifts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT pk_shifts PRIMARY KEY (id);


--
-- Name: stations pk_stations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT pk_stations PRIMARY KEY (id);


--
-- Name: temporary_day_manager_appointments pk_temporary_day_manager_appointments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temporary_day_manager_appointments
    ADD CONSTRAINT pk_temporary_day_manager_appointments PRIMARY KEY (id);


--
-- Name: users pk_users; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id);


--
-- Name: volunteer_level_history pk_volunteer_level_history; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.volunteer_level_history
    ADD CONSTRAINT pk_volunteer_level_history PRIMARY KEY (id);


--
-- Name: activity_days uq_activity_days_station_id_local_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_days
    ADD CONSTRAINT uq_activity_days_station_id_local_date UNIQUE (station_id, local_date);


--
-- Name: ambulance_slots uq_ambulance_slots_version_id_slot_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ambulance_slots
    ADD CONSTRAINT uq_ambulance_slots_version_id_slot_key UNIQUE (version_id, slot_key);


--
-- Name: assignment_cancellations uq_assignment_cancellations_version_id_shift_id_user_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_cancellations
    ADD CONSTRAINT uq_assignment_cancellations_version_id_shift_id_user_id UNIQUE (version_id, shift_id, user_id);


--
-- Name: assignments uq_assignment_person_shift; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT uq_assignment_person_shift UNIQUE (version_id, shift_id, user_id);


--
-- Name: boarding_entries uq_boarding_entries_version_id_user_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boarding_entries
    ADD CONSTRAINT uq_boarding_entries_version_id_user_id UNIQUE (version_id, user_id);


--
-- Name: mentoring_summaries uq_mentoring_summaries_trainee_id_shift_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentoring_summaries
    ADD CONSTRAINT uq_mentoring_summaries_trainee_id_shift_id UNIQUE (trainee_id, shift_id);


--
-- Name: notification_outbox uq_notification_outbox_dedup_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT uq_notification_outbox_dedup_key UNIQUE (dedup_key);


--
-- Name: push_subscriptions uq_push_subscriptions_endpoint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT uq_push_subscriptions_endpoint UNIQUE (endpoint);


--
-- Name: registration_choices uq_registration_choices_registration_id_shift_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_choices
    ADD CONSTRAINT uq_registration_choices_registration_id_shift_id UNIQUE (registration_id, shift_id);


--
-- Name: registrations uq_registrations_user_id_activity_day_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registrations
    ADD CONSTRAINT uq_registrations_user_id_activity_day_id UNIQUE (user_id, activity_day_id);


--
-- Name: rides uq_rides_trainee_id_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT uq_rides_trainee_id_seq UNIQUE (trainee_id, seq);


--
-- Name: schedule_versions uq_schedule_versions_activity_day_id_revision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT uq_schedule_versions_activity_day_id_revision UNIQUE (activity_day_id, revision);


--
-- Name: sessions uq_sessions_token_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash);


--
-- Name: shifts uq_shifts_activity_day_id_kind; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT uq_shifts_activity_day_id_kind UNIQUE (activity_day_id, kind);


--
-- Name: ambulance_slots uq_slot_identity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ambulance_slots
    ADD CONSTRAINT uq_slot_identity UNIQUE (id, version_id, shift_id);


--
-- Name: stations uq_stations_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT uq_stations_name UNIQUE (name);


--
-- Name: users uq_users_email; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_email UNIQUE (email);


--
-- Name: ix_activity_days_local_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_activity_days_local_date ON public.activity_days USING btree (local_date);


--
-- Name: ix_ambulance_slots_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ambulance_slots_shift_id ON public.ambulance_slots USING btree (shift_id);


--
-- Name: ix_ambulance_slots_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ambulance_slots_version_id ON public.ambulance_slots USING btree (version_id);


--
-- Name: ix_assignment_cancellations_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_cancellations_version_id ON public.assignment_cancellations USING btree (version_id);


--
-- Name: ix_assignments_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_user_id ON public.assignments USING btree (user_id);


--
-- Name: ix_assignments_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_version_id ON public.assignments USING btree (version_id);


--
-- Name: ix_audit_events_actor_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_events_actor_user_id ON public.audit_events USING btree (actor_user_id);


--
-- Name: ix_authority_roles_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_authority_roles_user_id ON public.authority_roles USING btree (user_id);


--
-- Name: ix_availability_attention_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_availability_attention_shift_id ON public.availability_attention USING btree (shift_id);


--
-- Name: ix_boarding_entries_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_boarding_entries_version_id ON public.boarding_entries USING btree (version_id);


--
-- Name: ix_day_memberships_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_day_memberships_user_id ON public.day_memberships USING btree (user_id);


--
-- Name: ix_invitations_activity_day_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invitations_activity_day_id ON public.invitations USING btree (activity_day_id);


--
-- Name: ix_invitations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invitations_user_id ON public.invitations USING btree (user_id);


--
-- Name: ix_manager_added_participants_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_manager_added_participants_shift_id ON public.manager_added_participants USING btree (shift_id);


--
-- Name: ix_manager_added_participants_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_manager_added_participants_user_id ON public.manager_added_participants USING btree (user_id);


--
-- Name: ix_manager_day_scopes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_manager_day_scopes_user_id ON public.manager_day_scopes USING btree (user_id);


--
-- Name: ix_mentor_access_extensions_mentor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mentor_access_extensions_mentor_id ON public.mentor_access_extensions USING btree (mentor_id);


--
-- Name: ix_mentor_access_extensions_trainee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mentor_access_extensions_trainee_id ON public.mentor_access_extensions USING btree (trainee_id);


--
-- Name: ix_mentoring_summaries_trainee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mentoring_summaries_trainee_id ON public.mentoring_summaries USING btree (trainee_id);


--
-- Name: ix_notification_outbox_recipient_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_outbox_recipient_user_id ON public.notification_outbox USING btree (recipient_user_id);


--
-- Name: ix_outbox_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_outbox_due ON public.notification_outbox USING btree (status, next_attempt_at);


--
-- Name: ix_personal_notes_author_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_notes_author_id ON public.personal_notes USING btree (author_id);


--
-- Name: ix_progress_corrections_trainee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_progress_corrections_trainee_id ON public.progress_corrections USING btree (trainee_id);


--
-- Name: ix_push_subscriptions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_push_subscriptions_user_id ON public.push_subscriptions USING btree (user_id);


--
-- Name: ix_registration_choices_registration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registration_choices_registration_id ON public.registration_choices USING btree (registration_id);


--
-- Name: ix_registration_choices_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registration_choices_shift_id ON public.registration_choices USING btree (shift_id);


--
-- Name: ix_registration_requests_activity_day_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registration_requests_activity_day_id ON public.registration_requests USING btree (activity_day_id);


--
-- Name: ix_registration_requests_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registration_requests_shift_id ON public.registration_requests USING btree (shift_id);


--
-- Name: ix_registration_requests_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registration_requests_user_id ON public.registration_requests USING btree (user_id);


--
-- Name: ix_registrations_activity_day_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registrations_activity_day_id ON public.registrations USING btree (activity_day_id);


--
-- Name: ix_registrations_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_registrations_user_id ON public.registrations USING btree (user_id);


--
-- Name: ix_rides_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rides_shift_id ON public.rides USING btree (shift_id);


--
-- Name: ix_rides_trainee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rides_trainee_id ON public.rides USING btree (trainee_id);


--
-- Name: ix_schedule_versions_activity_day_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedule_versions_activity_day_id ON public.schedule_versions USING btree (activity_day_id);


--
-- Name: ix_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sessions_user_id ON public.sessions USING btree (user_id);


--
-- Name: ix_shifts_activity_day_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shifts_activity_day_id ON public.shifts USING btree (activity_day_id);


--
-- Name: ix_temporary_day_manager_appointments_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_temporary_day_manager_appointments_user_id ON public.temporary_day_manager_appointments USING btree (user_id);


--
-- Name: ix_volunteer_level_history_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_volunteer_level_history_user_id ON public.volunteer_level_history USING btree (user_id);


--
-- Name: uq_added_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_added_current ON public.manager_added_participants USING btree (shift_id, user_id) WHERE (removed_at IS NULL);


--
-- Name: uq_attention_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_attention_pending ON public.availability_attention USING btree (shift_id, user_id) WHERE ((status)::text = 'pending'::text);


--
-- Name: uq_invitation_open; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_invitation_open ON public.invitations USING btree (user_id, activity_day_id, kind) WHERE ((status)::text = ANY ((ARRAY['pending'::character varying, 'confirmed'::character varying])::text[]));


--
-- Name: uq_level_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_level_current ON public.volunteer_level_history USING btree (user_id) WHERE (valid_to IS NULL);


--
-- Name: uq_membership_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_membership_current ON public.day_memberships USING btree (user_id) WHERE (valid_to IS NULL);


--
-- Name: uq_one_draft_per_day; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_one_draft_per_day ON public.schedule_versions USING btree (activity_day_id) WHERE ((status)::text = 'draft'::text);


--
-- Name: uq_one_permanent_manager_per_weekday; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_one_permanent_manager_per_weekday ON public.manager_day_scopes USING btree (weekday) WHERE (valid_to IS NULL);


--
-- Name: uq_one_permanent_scope_per_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_one_permanent_scope_per_user ON public.manager_day_scopes USING btree (user_id) WHERE (valid_to IS NULL);


--
-- Name: uq_role_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_role_current ON public.authority_roles USING btree (user_id, role) WHERE (valid_to IS NULL);


--
-- Name: uq_scope_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_scope_current ON public.manager_day_scopes USING btree (user_id, weekday) WHERE (valid_to IS NULL);


--
-- Name: uq_template_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_template_current ON public.registration_templates USING btree (weekday) WHERE (valid_to IS NULL);


--
-- Name: ambulance_slots trg_ambulance_slots_draft_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ambulance_slots_draft_only BEFORE INSERT OR DELETE OR UPDATE ON public.ambulance_slots FOR EACH ROW EXECUTE FUNCTION public.enforce_draft_only();


--
-- Name: assignments trg_assignments_draft_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_assignments_draft_only BEFORE INSERT OR DELETE OR UPDATE ON public.assignments FOR EACH ROW EXECUTE FUNCTION public.enforce_draft_only();


--
-- Name: activity_days fk_activity_days_latest_published; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_days
    ADD CONSTRAINT fk_activity_days_latest_published FOREIGN KEY (latest_published_version_id) REFERENCES public.schedule_versions(id);


--
-- Name: activity_days fk_activity_days_station_id_stations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_days
    ADD CONSTRAINT fk_activity_days_station_id_stations FOREIGN KEY (station_id) REFERENCES public.stations(id);


--
-- Name: ambulance_slots fk_ambulance_slots_operator_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ambulance_slots
    ADD CONSTRAINT fk_ambulance_slots_operator_user_id_users FOREIGN KEY (operator_user_id) REFERENCES public.users(id);


--
-- Name: ambulance_slots fk_ambulance_slots_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ambulance_slots
    ADD CONSTRAINT fk_ambulance_slots_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: ambulance_slots fk_ambulance_slots_version_id_schedule_versions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ambulance_slots
    ADD CONSTRAINT fk_ambulance_slots_version_id_schedule_versions FOREIGN KEY (version_id) REFERENCES public.schedule_versions(id);


--
-- Name: assignment_cancellations fk_assignment_cancellations_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_cancellations
    ADD CONSTRAINT fk_assignment_cancellations_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: assignment_cancellations fk_assignment_cancellations_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_cancellations
    ADD CONSTRAINT fk_assignment_cancellations_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: assignment_cancellations fk_assignment_cancellations_version_id_schedule_versions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_cancellations
    ADD CONSTRAINT fk_assignment_cancellations_version_id_schedule_versions FOREIGN KEY (version_id) REFERENCES public.schedule_versions(id);


--
-- Name: assignments fk_assignment_slot_identity; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT fk_assignment_slot_identity FOREIGN KEY (slot_id, version_id, shift_id) REFERENCES public.ambulance_slots(id, version_id, shift_id);


--
-- Name: assignments fk_assignments_created_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT fk_assignments_created_by_id_users FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: assignments fk_assignments_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT fk_assignments_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: assignments fk_assignments_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT fk_assignments_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: assignments fk_assignments_version_id_schedule_versions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT fk_assignments_version_id_schedule_versions FOREIGN KEY (version_id) REFERENCES public.schedule_versions(id);


--
-- Name: audit_events fk_audit_events_actor_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT fk_audit_events_actor_user_id_users FOREIGN KEY (actor_user_id) REFERENCES public.users(id);


--
-- Name: authority_roles fk_authority_roles_granted_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authority_roles
    ADD CONSTRAINT fk_authority_roles_granted_by_id_users FOREIGN KEY (granted_by_id) REFERENCES public.users(id);


--
-- Name: authority_roles fk_authority_roles_revoked_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authority_roles
    ADD CONSTRAINT fk_authority_roles_revoked_by_id_users FOREIGN KEY (revoked_by_id) REFERENCES public.users(id);


--
-- Name: authority_roles fk_authority_roles_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authority_roles
    ADD CONSTRAINT fk_authority_roles_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: availability_attention fk_availability_attention_published_version_id_schedule_4ec5; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_attention
    ADD CONSTRAINT fk_availability_attention_published_version_id_schedule_4ec5 FOREIGN KEY (published_version_id) REFERENCES public.schedule_versions(id);


--
-- Name: availability_attention fk_availability_attention_resolved_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_attention
    ADD CONSTRAINT fk_availability_attention_resolved_by_id_users FOREIGN KEY (resolved_by_id) REFERENCES public.users(id);


--
-- Name: availability_attention fk_availability_attention_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_attention
    ADD CONSTRAINT fk_availability_attention_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: availability_attention fk_availability_attention_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_attention
    ADD CONSTRAINT fk_availability_attention_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: boarding_entries fk_boarding_entries_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boarding_entries
    ADD CONSTRAINT fk_boarding_entries_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: boarding_entries fk_boarding_entries_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boarding_entries
    ADD CONSTRAINT fk_boarding_entries_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: boarding_entries fk_boarding_entries_version_id_schedule_versions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boarding_entries
    ADD CONSTRAINT fk_boarding_entries_version_id_schedule_versions FOREIGN KEY (version_id) REFERENCES public.schedule_versions(id);


--
-- Name: day_memberships fk_day_memberships_changed_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.day_memberships
    ADD CONSTRAINT fk_day_memberships_changed_by_id_users FOREIGN KEY (changed_by_id) REFERENCES public.users(id);


--
-- Name: day_memberships fk_day_memberships_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.day_memberships
    ADD CONSTRAINT fk_day_memberships_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: invitations fk_invitations_activity_day_id_activity_days; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT fk_invitations_activity_day_id_activity_days FOREIGN KEY (activity_day_id) REFERENCES public.activity_days(id);


--
-- Name: invitations fk_invitations_invited_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT fk_invitations_invited_by_id_users FOREIGN KEY (invited_by_id) REFERENCES public.users(id);


--
-- Name: invitations fk_invitations_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT fk_invitations_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: invitations fk_invitations_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT fk_invitations_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: manager_added_participants fk_manager_added_participants_added_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_added_participants
    ADD CONSTRAINT fk_manager_added_participants_added_by_id_users FOREIGN KEY (added_by_id) REFERENCES public.users(id);


--
-- Name: manager_added_participants fk_manager_added_participants_removed_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_added_participants
    ADD CONSTRAINT fk_manager_added_participants_removed_by_id_users FOREIGN KEY (removed_by_id) REFERENCES public.users(id);


--
-- Name: manager_added_participants fk_manager_added_participants_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_added_participants
    ADD CONSTRAINT fk_manager_added_participants_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: manager_added_participants fk_manager_added_participants_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_added_participants
    ADD CONSTRAINT fk_manager_added_participants_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: manager_day_scopes fk_manager_day_scopes_granted_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_day_scopes
    ADD CONSTRAINT fk_manager_day_scopes_granted_by_id_users FOREIGN KEY (granted_by_id) REFERENCES public.users(id);


--
-- Name: manager_day_scopes fk_manager_day_scopes_revoked_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_day_scopes
    ADD CONSTRAINT fk_manager_day_scopes_revoked_by_id_users FOREIGN KEY (revoked_by_id) REFERENCES public.users(id);


--
-- Name: manager_day_scopes fk_manager_day_scopes_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manager_day_scopes
    ADD CONSTRAINT fk_manager_day_scopes_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: mentor_access_extensions fk_mentor_access_extensions_granted_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentor_access_extensions
    ADD CONSTRAINT fk_mentor_access_extensions_granted_by_id_users FOREIGN KEY (granted_by_id) REFERENCES public.users(id);


--
-- Name: mentor_access_extensions fk_mentor_access_extensions_mentor_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentor_access_extensions
    ADD CONSTRAINT fk_mentor_access_extensions_mentor_id_users FOREIGN KEY (mentor_id) REFERENCES public.users(id);


--
-- Name: mentor_access_extensions fk_mentor_access_extensions_revoked_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentor_access_extensions
    ADD CONSTRAINT fk_mentor_access_extensions_revoked_by_id_users FOREIGN KEY (revoked_by_id) REFERENCES public.users(id);


--
-- Name: mentor_access_extensions fk_mentor_access_extensions_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentor_access_extensions
    ADD CONSTRAINT fk_mentor_access_extensions_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: mentor_access_extensions fk_mentor_access_extensions_trainee_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentor_access_extensions
    ADD CONSTRAINT fk_mentor_access_extensions_trainee_id_users FOREIGN KEY (trainee_id) REFERENCES public.users(id);


--
-- Name: mentoring_summaries fk_mentoring_summaries_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentoring_summaries
    ADD CONSTRAINT fk_mentoring_summaries_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: mentoring_summaries fk_mentoring_summaries_trainee_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentoring_summaries
    ADD CONSTRAINT fk_mentoring_summaries_trainee_id_users FOREIGN KEY (trainee_id) REFERENCES public.users(id);


--
-- Name: mentoring_summaries fk_mentoring_summaries_updated_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mentoring_summaries
    ADD CONSTRAINT fk_mentoring_summaries_updated_by_id_users FOREIGN KEY (updated_by_id) REFERENCES public.users(id);


--
-- Name: notification_outbox fk_notification_outbox_recipient_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT fk_notification_outbox_recipient_user_id_users FOREIGN KEY (recipient_user_id) REFERENCES public.users(id);


--
-- Name: personal_notes fk_personal_notes_author_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_notes
    ADD CONSTRAINT fk_personal_notes_author_id_users FOREIGN KEY (author_id) REFERENCES public.users(id);


--
-- Name: personal_notes fk_personal_notes_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_notes
    ADD CONSTRAINT fk_personal_notes_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: progress_corrections fk_progress_corrections_created_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.progress_corrections
    ADD CONSTRAINT fk_progress_corrections_created_by_id_users FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: progress_corrections fk_progress_corrections_trainee_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.progress_corrections
    ADD CONSTRAINT fk_progress_corrections_trainee_id_users FOREIGN KEY (trainee_id) REFERENCES public.users(id);


--
-- Name: push_subscriptions fk_push_subscriptions_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT fk_push_subscriptions_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: queue_order fk_queue_order_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_order
    ADD CONSTRAINT fk_queue_order_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: queue_order fk_queue_order_updated_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_order
    ADD CONSTRAINT fk_queue_order_updated_by_id_users FOREIGN KEY (updated_by_id) REFERENCES public.users(id);


--
-- Name: queue_order fk_queue_order_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_order
    ADD CONSTRAINT fk_queue_order_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: registration_choices fk_registration_choices_cancelled_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_choices
    ADD CONSTRAINT fk_registration_choices_cancelled_by_id_users FOREIGN KEY (cancelled_by_id) REFERENCES public.users(id);


--
-- Name: registration_choices fk_registration_choices_registration_id_registrations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_choices
    ADD CONSTRAINT fk_registration_choices_registration_id_registrations FOREIGN KEY (registration_id) REFERENCES public.registrations(id);


--
-- Name: registration_choices fk_registration_choices_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_choices
    ADD CONSTRAINT fk_registration_choices_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: registration_requests fk_registration_requests_activity_day_id_activity_days; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_requests
    ADD CONSTRAINT fk_registration_requests_activity_day_id_activity_days FOREIGN KEY (activity_day_id) REFERENCES public.activity_days(id);


--
-- Name: registration_requests fk_registration_requests_resolved_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_requests
    ADD CONSTRAINT fk_registration_requests_resolved_by_id_users FOREIGN KEY (resolved_by_id) REFERENCES public.users(id);


--
-- Name: registration_requests fk_registration_requests_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_requests
    ADD CONSTRAINT fk_registration_requests_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: registration_requests fk_registration_requests_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_requests
    ADD CONSTRAINT fk_registration_requests_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: registration_templates fk_registration_templates_created_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration_templates
    ADD CONSTRAINT fk_registration_templates_created_by_id_users FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: registrations fk_registrations_activity_day_id_activity_days; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registrations
    ADD CONSTRAINT fk_registrations_activity_day_id_activity_days FOREIGN KEY (activity_day_id) REFERENCES public.activity_days(id);


--
-- Name: registrations fk_registrations_updated_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registrations
    ADD CONSTRAINT fk_registrations_updated_by_id_users FOREIGN KEY (updated_by_id) REFERENCES public.users(id);


--
-- Name: registrations fk_registrations_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registrations
    ADD CONSTRAINT fk_registrations_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: ride_assessments fk_ride_assessments_ride_id_rides; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ride_assessments
    ADD CONSTRAINT fk_ride_assessments_ride_id_rides FOREIGN KEY (ride_id) REFERENCES public.rides(id);


--
-- Name: ride_assessments fk_ride_assessments_updated_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ride_assessments
    ADD CONSTRAINT fk_ride_assessments_updated_by_id_users FOREIGN KEY (updated_by_id) REFERENCES public.users(id);


--
-- Name: ride_contributors fk_ride_contributors_ride_id_rides; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ride_contributors
    ADD CONSTRAINT fk_ride_contributors_ride_id_rides FOREIGN KEY (ride_id) REFERENCES public.rides(id);


--
-- Name: ride_contributors fk_ride_contributors_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ride_contributors
    ADD CONSTRAINT fk_ride_contributors_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: rides fk_rides_cancelled_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT fk_rides_cancelled_by_id_users FOREIGN KEY (cancelled_by_id) REFERENCES public.users(id);


--
-- Name: rides fk_rides_created_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT fk_rides_created_by_id_users FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: rides fk_rides_duplicate_of_id_rides; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT fk_rides_duplicate_of_id_rides FOREIGN KEY (duplicate_of_id) REFERENCES public.rides(id);


--
-- Name: rides fk_rides_shift_id_shifts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT fk_rides_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: rides fk_rides_trainee_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rides
    ADD CONSTRAINT fk_rides_trainee_id_users FOREIGN KEY (trainee_id) REFERENCES public.users(id);


--
-- Name: schedule_versions fk_schedule_versions_activity_day_id_activity_days; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT fk_schedule_versions_activity_day_id_activity_days FOREIGN KEY (activity_day_id) REFERENCES public.activity_days(id);


--
-- Name: schedule_versions fk_schedule_versions_base_published_version_id_schedule_b193; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT fk_schedule_versions_base_published_version_id_schedule_b193 FOREIGN KEY (base_published_version_id) REFERENCES public.schedule_versions(id);


--
-- Name: schedule_versions fk_schedule_versions_created_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT fk_schedule_versions_created_by_id_users FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: schedule_versions fk_schedule_versions_last_edit_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT fk_schedule_versions_last_edit_by_id_users FOREIGN KEY (last_edit_by_id) REFERENCES public.users(id);


--
-- Name: schedule_versions fk_schedule_versions_published_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_versions
    ADD CONSTRAINT fk_schedule_versions_published_by_id_users FOREIGN KEY (published_by_id) REFERENCES public.users(id);


--
-- Name: sessions fk_sessions_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT fk_sessions_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: shifts fk_shifts_activity_day_id_activity_days; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT fk_shifts_activity_day_id_activity_days FOREIGN KEY (activity_day_id) REFERENCES public.activity_days(id);


--
-- Name: temporary_day_manager_appointments fk_temp_dm_created_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temporary_day_manager_appointments
    ADD CONSTRAINT fk_temp_dm_created_by_id_users FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: temporary_day_manager_appointments fk_temp_dm_revoked_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temporary_day_manager_appointments
    ADD CONSTRAINT fk_temp_dm_revoked_by_id_users FOREIGN KEY (revoked_by_id) REFERENCES public.users(id);


--
-- Name: temporary_day_manager_appointments fk_temp_dm_updated_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temporary_day_manager_appointments
    ADD CONSTRAINT fk_temp_dm_updated_by_id_users FOREIGN KEY (updated_by_id) REFERENCES public.users(id);


--
-- Name: temporary_day_manager_appointments fk_temp_dm_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temporary_day_manager_appointments
    ADD CONSTRAINT fk_temp_dm_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: users fk_users_approved_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_approved_by_id_users FOREIGN KEY (approved_by_id) REFERENCES public.users(id);


--
-- Name: volunteer_level_history fk_volunteer_level_history_changed_by_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.volunteer_level_history
    ADD CONSTRAINT fk_volunteer_level_history_changed_by_id_users FOREIGN KEY (changed_by_id) REFERENCES public.users(id);


--
-- Name: volunteer_level_history fk_volunteer_level_history_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.volunteer_level_history
    ADD CONSTRAINT fk_volunteer_level_history_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--
'''


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    raise NotImplementedError("baseline migration: restore from a backup to go back")
