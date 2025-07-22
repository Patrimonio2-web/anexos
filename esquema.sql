--
-- PostgreSQL database dump
--

-- Dumped from database version 16.9 (Debian 16.9-1.pgdg120+1)
-- Dumped by pg_dump version 17.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: patrimonio_ppfk_user
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO patrimonio_ppfk_user;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: anexos; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.anexos (
    id integer NOT NULL,
    nombre character varying(255) NOT NULL,
    direccion text
);


ALTER TABLE public.anexos OWNER TO patrimonio_ppfk_user;

--
-- Name: clases_bienes; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.clases_bienes (
    id_clase integer NOT NULL,
    id_rubro integer,
    descripcion text NOT NULL
);


ALTER TABLE public.clases_bienes OWNER TO patrimonio_ppfk_user;

--
-- Name: mobiliario; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.mobiliario (
    id character varying(50) NOT NULL,
    ubicacion_id integer,
    descripcion text,
    resolucion text,
    fecha_resolucion date,
    estado_conservacion character varying(20),
    no_dado boolean DEFAULT false,
    para_reparacion boolean DEFAULT false,
    para_baja boolean DEFAULT false,
    faltante boolean DEFAULT false,
    sobrante boolean DEFAULT false,
    problema_etiqueta boolean DEFAULT false,
    comentarios text,
    foto_url character varying(255),
    fecha_creacion timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    rubro_id integer,
    clase_bien_id integer,
    estado_control character varying(20),
    historial_movimientos text
);


ALTER TABLE public.mobiliario OWNER TO patrimonio_ppfk_user;

--
-- Name: movimientos_altas; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.movimientos_altas (
    id integer NOT NULL,
    fecha_alta date,
    cantidad integer,
    concepto text,
    disposicion text,
    valor_unitario numeric(12,2),
    valor_total numeric(12,2),
    causa_alta text,
    codigo_presup text,
    clase text,
    identidad text,
    rubro text,
    mes_planilla text,
    anio_planilla text,
    id_rubro integer,
    id_clase integer,
    fecha_resolucion date
);


ALTER TABLE public.movimientos_altas OWNER TO patrimonio_ppfk_user;

--
-- Name: movimientos_altas_id_seq; Type: SEQUENCE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE SEQUENCE public.movimientos_altas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.movimientos_altas_id_seq OWNER TO patrimonio_ppfk_user;

--
-- Name: movimientos_altas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER SEQUENCE public.movimientos_altas_id_seq OWNED BY public.movimientos_altas.id;


--
-- Name: persona; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.persona (
    id integer NOT NULL,
    nombre character varying(100) NOT NULL,
    apellido character varying(100) NOT NULL
);


ALTER TABLE public.persona OWNER TO patrimonio_ppfk_user;

--
-- Name: persona_id_seq; Type: SEQUENCE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE SEQUENCE public.persona_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.persona_id_seq OWNER TO patrimonio_ppfk_user;

--
-- Name: persona_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER SEQUENCE public.persona_id_seq OWNED BY public.persona.id;


--
-- Name: rubros; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.rubros (
    id_rubro integer NOT NULL,
    nombre text NOT NULL
);


ALTER TABLE public.rubros OWNER TO patrimonio_ppfk_user;

--
-- Name: subdependencias; Type: TABLE; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE TABLE public.subdependencias (
    id integer NOT NULL,
    id_anexo integer NOT NULL,
    nombre character varying(255) NOT NULL,
    piso integer
);


ALTER TABLE public.subdependencias OWNER TO patrimonio_ppfk_user;

--
-- Name: vista_mobiliario_con_ubicacion; Type: VIEW; Schema: public; Owner: patrimonio_ppfk_user
--

CREATE VIEW public.vista_mobiliario_con_ubicacion AS
 SELECT m.id AS mobiliario_id,
    m.descripcion,
    m.estado_conservacion,
    m.no_dado,
    m.para_reparacion,
    m.para_baja,
    m.faltante,
    m.sobrante,
    m.problema_etiqueta,
    m.comentarios,
    m.fecha_resolucion,
    m.resolucion,
    m.foto_url,
    m.fecha_creacion,
    m.fecha_actualizacion,
    s.id AS subdependencia_id,
    s.nombre AS subdependencia,
    a.id AS anexo_id,
    a.nombre AS anexo
   FROM ((public.mobiliario m
     JOIN public.subdependencias s ON ((m.ubicacion_id = s.id)))
     JOIN public.anexos a ON ((s.id_anexo = a.id)));


ALTER VIEW public.vista_mobiliario_con_ubicacion OWNER TO patrimonio_ppfk_user;

--
-- Name: movimientos_altas id; Type: DEFAULT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.movimientos_altas ALTER COLUMN id SET DEFAULT nextval('public.movimientos_altas_id_seq'::regclass);


--
-- Name: persona id; Type: DEFAULT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.persona ALTER COLUMN id SET DEFAULT nextval('public.persona_id_seq'::regclass);


--
-- Name: anexos anexos_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.anexos
    ADD CONSTRAINT anexos_pkey PRIMARY KEY (id);


--
-- Name: clases_bienes clases_bienes_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.clases_bienes
    ADD CONSTRAINT clases_bienes_pkey PRIMARY KEY (id_clase);


--
-- Name: mobiliario mobiliario_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.mobiliario
    ADD CONSTRAINT mobiliario_pkey PRIMARY KEY (id);


--
-- Name: movimientos_altas movimientos_altas_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.movimientos_altas
    ADD CONSTRAINT movimientos_altas_pkey PRIMARY KEY (id);


--
-- Name: persona persona_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.persona
    ADD CONSTRAINT persona_pkey PRIMARY KEY (id);


--
-- Name: rubros rubros_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.rubros
    ADD CONSTRAINT rubros_pkey PRIMARY KEY (id_rubro);


--
-- Name: subdependencias subdependencias_pkey; Type: CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.subdependencias
    ADD CONSTRAINT subdependencias_pkey PRIMARY KEY (id);


--
-- Name: clases_bienes clases_bienes_id_rubro_fkey; Type: FK CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.clases_bienes
    ADD CONSTRAINT clases_bienes_id_rubro_fkey FOREIGN KEY (id_rubro) REFERENCES public.rubros(id_rubro);


--
-- Name: mobiliario mobiliario_clase_bien_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.mobiliario
    ADD CONSTRAINT mobiliario_clase_bien_id_fkey FOREIGN KEY (clase_bien_id) REFERENCES public.clases_bienes(id_clase) ON DELETE SET NULL;


--
-- Name: mobiliario mobiliario_rubro_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.mobiliario
    ADD CONSTRAINT mobiliario_rubro_id_fkey FOREIGN KEY (rubro_id) REFERENCES public.rubros(id_rubro) ON DELETE SET NULL;


--
-- Name: mobiliario mobiliario_ubicacion_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.mobiliario
    ADD CONSTRAINT mobiliario_ubicacion_id_fkey FOREIGN KEY (ubicacion_id) REFERENCES public.subdependencias(id) ON DELETE SET NULL;


--
-- Name: subdependencias subdependencias_id_anexo_fkey; Type: FK CONSTRAINT; Schema: public; Owner: patrimonio_ppfk_user
--

ALTER TABLE ONLY public.subdependencias
    ADD CONSTRAINT subdependencias_id_anexo_fkey FOREIGN KEY (id_anexo) REFERENCES public.anexos(id) ON DELETE CASCADE;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON SEQUENCES TO patrimonio_ppfk_user;


--
-- Name: DEFAULT PRIVILEGES FOR TYPES; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON TYPES TO patrimonio_ppfk_user;


--
-- Name: DEFAULT PRIVILEGES FOR FUNCTIONS; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON FUNCTIONS TO patrimonio_ppfk_user;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLES TO patrimonio_ppfk_user;


--
-- PostgreSQL database dump complete
--

